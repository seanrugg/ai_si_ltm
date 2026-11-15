"""Transcripts and replies from a wire-pod voice server (#312).

Source, confirmed in wire-pod's code (kercre123/wire-pod, commit dc6ae0c):

- ``GET /api/get_logs`` returns ``logger.LogList``: the last 50 lines written
  with ``logger.LogUI`` (``chipper/pkg/wirepod/config-ws/webserver.go``
  ``handleGetLogs``; ``chipper/pkg/logger/logger.go``). This is the log shown
  in wire-pod's web UI.
- ``GET /api/get_debug_logs`` returns ``logger.LogTrayList``: the last 200
  lines written with ``logger.Println`` (``handleGetDebugLogs``).
- Every line is ``"2006.01.02 15:04:05: <text>"`` (Go time layout, server local time).

Lines this adapter maps, each to exactly one record (anything else is ignored):

| Log | Line (source) | Record |
|---|---|---|
| UI | ``Intent matched: <intent>, transcribed text: '<text>', device: <esn>`` (ttr/matchIntentSend.go) | ``perception`` tags voice,heard |
| UI | ``Parameters sent: <params>`` (ttr/matchIntentSend.go) | ``event`` tags voice,intent-params |
| UI | ``LLM response for <esn>: <text>`` (ttr/kgsim.go, kgsim_cmds.go) | ``communication`` tags voice,reply,llm |
| debug | ``Bot <esn> Custom Intent Matched: <name> - <description> - <intent>`` (ttr/matchIntentSend.go) | ``event`` tags voice,custom-intent |
| debug | ``Making LLM request for device <esn>...`` (preqs/intent.go) | ``event`` tags voice,llm-request |

**The conversation chain (#408).** The ``heard`` perception, the LLM request
``event`` and the LLM reply ``communication`` are linked with ``refs``: the
request refs the heard it answers (``{"because": <heard>}``), the reply refs the
request (``{"because": <request>}``) and carries ``model`` and ``runs_on``. The
log line names no model, so these come from ``SIDECAR_COGNITION_MODELS``'s first
entry (``name@where``); without it they're left out, never guessed. wire-pod's
log lines (above) carry no request id, so matching is by time and device: the
newest unanswered heard within 30 s before the request, and the newest
unanswered request within 30 s before the reply. Lines from both logs are taken
in timestamp order. Unmatched records go out without ``refs`` and are counted
in a log line.

What the robot *says* from a custom intent's Lua (``sayText``) is not logged by
wire-pod, so it can't be recorded from here. A custom intent named in
``SIDECAR_STATUS_REPORT_INTENT`` gets the extra tag ``status-report`` and, if
``SIDECAR_HEALTH_STATE`` points at the hub's health file (#349), the time of the
reading it would have spoken (``health_reading_at``), so it can be matched to
the hub's health record. The numbers are never parsed from speech.

Settings:
  WIREPOD_URL            (default http://127.0.0.1:8080)
  WIREPOD_ESN            only lines for this robot's serial (recommended if wire-pod serves several)
  WIREPOD_POLL_SECONDS   (default 2)
  SIDECAR_STATUS_REPORT_INTENT, SIDECAR_HEALTH_STATE  (optional, above)
  SIDECAR_COGNITION_MODELS  (its first "name@where" entry: the reply's model and runs_on)

The logs are rolling buffers, so a line is recorded once (by timestamp and
text), and lines older than the sidecar's start are skipped. Two identical
lines in the same second count as one.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from datetime import datetime
from typing import Callable

from adapters.base import Adapter, Emit, Item

LINE = re.compile(r"^(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}): (.*)$")
INTENT = re.compile(r"^Intent matched: (?P<intent>.*?), transcribed text: '(?P<text>.*)', device: (?P<esn>\S+)$")
PARAMS = re.compile(r"^Parameters sent: (?P<params>.*)$")
LLM = re.compile(r"^LLM response for (?P<esn>[^:\s]+): (?P<text>.*)$")
CUSTOM = re.compile(r"^Bot (?P<esn>\S+) Custom Intent Matched: (?P<name>.*?) - (?P<description>.*?) - (?P<intent>\S*)$")
LLM_REQ = re.compile(r"^Making LLM request for device (?P<esn>\S+?)\.\.\.$")
TS_FORMAT = "%Y.%m.%d %H:%M:%S"
MATCH_SECONDS = 30
# Within one second, the order the conversation happens in (stable otherwise).
RANK = ((INTENT, 0), (PARAMS, 1), (CUSTOM, 2), (LLM_REQ, 3), (LLM, 4))


def first_model(spec: str | None) -> tuple[str | None, str | None]:
    """``(name, runs_on)`` of the first ``name@where`` in ``SIDECAR_COGNITION_MODELS``."""
    for part in (spec or "").split(","):
        name, _, where = part.strip().partition("@")
        if name.strip():
            return name.strip()[:64], (where.strip() or "bridge")[:64]
    return None, None


def parse_lines(text: str) -> list[tuple[str, str]]:
    out = []
    for raw in (text or "").splitlines():
        m = LINE.match(raw.rstrip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


class WirePodLogs(Adapter):
    name = "wirepod"

    def __init__(self, url: str = "http://127.0.0.1:8080", esn: str | None = None, poll_seconds: float = 2.0,
                 status_intent: str | None = None, health_state: str | None = None,
                 fetch: Callable[[str], str] | None = None, started: datetime | None = None,
                 model: str | None = None, runs_on: str | None = None):
        self.url = url.rstrip("/")
        self.esn = esn or None
        self.poll_seconds = poll_seconds
        self.status_intent = status_intent or None
        self.health_state = health_state or None
        self.fetch = fetch or (lambda u: urllib.request.urlopen(u, timeout=5).read().decode("utf-8", "replace"))  # noqa: S310
        self.started = started or datetime.now()
        self.seen: set[tuple[str, str]] = set()
        self.last_intent_esn: str | None = None
        self.model, self.runs_on = model or None, runs_on or None
        self.n = 0
        self.heard: list[tuple[datetime, str | None, str]] = []     # unanswered: (at, device, key)
        self.requests: list[tuple[datetime, str | None, str]] = []
        self.unmatched = {"llm-request": 0, "llm-reply": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, env) -> "WirePodLogs":
        model, runs_on = first_model(env.get("SIDECAR_COGNITION_MODELS"))
        return cls(env.get("WIREPOD_URL", "http://127.0.0.1:8080"), env.get("WIREPOD_ESN"),
                   float(env.get("WIREPOD_POLL_SECONDS", "2")), env.get("SIDECAR_STATUS_REPORT_INTENT"),
                   env.get("SIDECAR_HEALTH_STATE"), model=model, runs_on=runs_on)

    def start(self, emit: Emit) -> None:
        def run():
            while not self._stop.wait(self.poll_seconds):
                try:
                    self.poll_once(emit)
                except Exception as exc:  # noqa: BLE001 - wire-pod down: try again next time
                    logging.warning("wirepod: %s", exc)
        self._thread = threading.Thread(target=run, name="wirepod-logs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _mine(self, esn: str | None) -> bool:
        return self.esn is None or esn is None or esn == self.esn

    def poll_once(self, emit: Emit) -> int:
        """Fetch both logs and emit an item for each new mapped line. Returns items emitted."""
        n = 0
        new = []
        for path, log in (("/api/get_logs", "ui"), ("/api/get_debug_logs", "debug")):
            for ts, text in parse_lines(self.fetch(self.url + path)):
                key = (ts, text)
                if key in self.seen:
                    continue
                self.seen.add(key)
                try:
                    if datetime.strptime(ts, TS_FORMAT) < self.started.replace(microsecond=0):
                        continue
                except ValueError:
                    continue
                rank = next((r for rx, r in RANK if rx.match(text)), 5)
                new.append((ts, rank, len(new), log, text))
        unmatched = dict(self.unmatched)
        for ts, _, _, log, text in sorted(new):  # both logs, in time order
            item = self.map_line(log, ts, text)
            if item is not None:
                emit(item)
                n += 1
        if self.unmatched != unmatched:
            logging.info("wirepod: sent without refs so far: %d LLM requests with no heard, %d replies with no "
                         "request (within %d s)", self.unmatched["llm-request"], self.unmatched["llm-reply"],
                         MATCH_SECONDS)
        if len(self.seen) > 2000:  # both buffers hold 250 lines at most
            self.seen = set(sorted(self.seen)[-1000:])
        return n

    def map_line(self, log: str, ts: str, text: str) -> Item | None:
        base = {"source": "wire-pod", "log": log, "logged_at": ts}
        if log == "ui":
            m = INTENT.match(text)
            if m:
                self.last_intent_esn = m["esn"]
                if not self._mine(m["esn"]):
                    return None
                key = self._key("heard")
                self.heard.append((self._at(ts), m["esn"], key))
                return Item("record", "perception", f"heard: {m['text']}"[:280], ["voice", "heard"],
                            {**base, "transcript": m["text"], "intent": m["intent"], "device": m["esn"]}, key=key)
            m = PARAMS.match(text)
            if m:
                if not self._mine(self.last_intent_esn):
                    return None
                return Item("record", "event", "intent parameters", ["voice", "intent-params"],
                            {**base, "parameters": m["params"], "device": self.last_intent_esn})
            m = LLM.match(text)
            if m:
                if not self._mine(m["esn"]):
                    return None
                payload = {**base, "text": m["text"], "device": m["esn"]}
                if self.model:
                    payload.update(model=self.model, runs_on=self.runs_on)
                req = self._match(self.requests, ts, m["esn"], "llm-reply")
                return Item("record", "communication", m["text"][:280] or "(empty reply)", ["voice", "reply", "llm"],
                            payload, refs={"because": req} if req else None)
            return None
        m = CUSTOM.match(text)
        if m:
            if not self._mine(m["esn"]):
                return None
            tags = ["voice", "custom-intent"]
            payload = {**base, "name": m["name"], "description": m["description"], "intent": m["intent"],
                       "device": m["esn"]}
            if self.status_intent and m["name"] == self.status_intent:
                tags.append("status-report")
                at = self._health_reading_at()
                if at is not None:
                    payload["health_reading_at"] = at
            return Item("record", "event", f"custom intent: {m['name']}"[:280], tags, payload)
        m = LLM_REQ.match(text)
        if m:
            if not self._mine(m["esn"]):
                return None
            heard = self._match(self.heard, ts, m["esn"], "llm-request")
            key = self._key("request")
            self.requests.append((self._at(ts), m["esn"], key))
            return Item("record", "event", "LLM request", ["voice", "llm-request"], {**base, "device": m["esn"]},
                        key=key, refs={"because": heard} if heard else None)
        return None

    def _key(self, what: str) -> str:
        self.n += 1
        return f"wp:{what}:{self.n}"

    @staticmethod
    def _at(ts: str) -> datetime:
        return datetime.strptime(ts, TS_FORMAT)

    def _match(self, pending: list, ts: str, device: str | None, what: str) -> str | None:
        """The key of the newest unanswered entry for ``device`` within 30 s before ``ts``; consumes it."""
        at = self._at(ts)
        pending[:] = [p for p in pending if (at - p[0]).total_seconds() <= MATCH_SECONDS]
        for i in range(len(pending) - 1, -1, -1):
            p_at, p_dev, key = pending[i]
            if p_at <= at and (device is None or p_dev is None or p_dev == device):
                del pending[i]
                return key
        self.unmatched[what] += 1
        return None

    def _health_reading_at(self) -> float | None:
        if not self.health_state:
            return None
        try:
            with open(self.health_state, encoding="utf-8") as f:
                at = json.load(f).get("at")
            return float(at) if at is not None else None
        except (OSError, ValueError, TypeError):
            return None
