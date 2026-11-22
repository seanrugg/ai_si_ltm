"""Record a Hermes Agent's sessions and tool calls as signed LTMS records (#469).

Plain Python with no Hermes imports, so the tests can drive it directly.
``__init__.py`` wires these methods to Hermes lifecycle hooks.

What gets recorded:

* ``on_session_start``  -> one ``event`` record (tags ``hermes,session``)
* ``post_tool_call``    -> one ``action`` record per tool call (tags ``hermes,tool-call``)
  with the tool name, redacted arguments, the raw result (truncated) and timing
* ``on_session_end``    -> one ``event`` record with the session's tool-call counts

The member's key lives in the ``LTMSClient`` and never appears in a record, a
log line, or an exception message. Nothing here ever raises into Hermes.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from cli_agent.client import LTMSClient, LTMSError
from cli_agent.outbox import Outbox
from ltms_contract import now_ts

log = logging.getLogger("ltms_recorder")

MAX_SUMMARY = 280                 # same limits as ltms_mcp
MAX_PAYLOAD_BYTES = 16 * 1024
MAX_ARGS_BYTES = 4096
MAX_PENDING = 1000                # unsigned events kept in memory while the chain cannot start

RECORD_MODES = ("full", "summary", "off")
DEFAULT_SETTINGS: dict[str, Any] = {
    "record_mode": "full",
    "peer_map": {},
    "max_result_bytes": 8192,
    "redact_keys": ["key", "token", "secret", "password", "authorization", "cookie"],
    "flush_interval_seconds": 5.0,
    "flush_batch": 20,
}
REDACTED = "[redacted]"
SESSION_TAGS = ["hermes", "session"]
TOOL_TAGS = ["hermes", "tool-call"]


# ---------------------------------------------------------------- pure helpers

def resolve_settings(raw: dict | None) -> dict[str, Any]:
    """Merge user settings over the defaults; bad values fall back to the default."""
    s = dict(DEFAULT_SETTINGS)
    raw = raw or {}
    mode = raw.get("record_mode")
    if mode in RECORD_MODES:
        s["record_mode"] = mode
    if isinstance(raw.get("peer_map"), dict):
        s["peer_map"] = {str(k): str(v) for k, v in raw["peer_map"].items() if k and v}
    for key in ("max_result_bytes", "flush_batch"):
        val = raw.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val > 0:
            s[key] = val
    val = raw.get("flush_interval_seconds")
    if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
        s["flush_interval_seconds"] = float(val)
    if isinstance(raw.get("redact_keys"), list):
        s["redact_keys"] = [str(k) for k in raw["redact_keys"] if str(k).strip()]
    s["max_result_bytes"] = min(s["max_result_bytes"], MAX_PAYLOAD_BYTES // 2)
    return s


def redact(value: Any, keys: list[str]) -> Any:
    """Copy ``value`` with the value of any dict key containing one of ``keys``
    (case-insensitive substring, at any depth) replaced by ``[redacted]``."""
    needles = [k.lower() for k in keys]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(n in str(k).lower() for n in needles):
                out[k] = REDACTED
            else:
                out[k] = redact(v, keys)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, keys) for v in value]
    return value


def json_safe(value: Any) -> Any:
    """A copy of ``value`` made only of JSON types (unknown objects become text)."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False))
    except (TypeError, ValueError):
        return str(value)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """Cut ``text`` to at most ``max_bytes`` UTF-8 bytes without splitting a character."""
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text, False
    return data[:max_bytes].decode("utf-8", errors="ignore"), True


def result_text(result: Any) -> str:
    """The tool result as Hermes returned it, as text (tool handlers return JSON strings)."""
    if isinstance(result, str):
        return result
    if isinstance(result, (bytes, bytearray)):
        return bytes(result).decode("utf-8", errors="replace")
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def looks_like_error(result: Any) -> bool:
    """Best-effort: did the tool report an error? (Hermes has no separate flag.)

    True for a JSON object with a truthy ``error``, ``success: false``,
    ``ok: false`` or ``is_error: true``, and for text starting with ``Error``.
    """
    obj = result
    if isinstance(result, str):
        stripped = result.strip()
        if stripped[:5].lower() == "error":
            return True
        try:
            obj = json.loads(stripped)
        except (ValueError, TypeError):
            return False
    if isinstance(obj, dict):
        if obj.get("error"):
            return True
        if obj.get("success") is False or obj.get("ok") is False:
            return True
        if obj.get("is_error") is True or obj.get("isError") is True:
            return True
    return False


def peers_for(tool_name: str, peer_map: dict[str, str]) -> list[str] | None:
    """The member a tool acts on, from the longest matching tool-name prefix."""
    best = None
    for prefix, member in peer_map.items():
        if tool_name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, member)
    return [best[1]] if best else None


def clip_summary(text: str) -> str:
    return text if len(text) <= MAX_SUMMARY else text[: MAX_SUMMARY - 1] + "…"


def tool_call_record(
    *,
    tool_name: str,
    args: Any,
    result: Any,
    task_id: Any,
    duration_ms: Any,
    session_id: str | None,
    model: str | None,
    settings: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Build ``(summary, payload, header)`` for one tool call. Never over the limits."""
    ok = not looks_like_error(result)
    ms = duration_ms if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool) else None
    timing = f" ({int(ms)} ms)" if ms is not None else ""
    summary = clip_summary(f"{tool_name} {'ok' if ok else 'error'}{timing}")

    payload: dict[str, Any] = {
        "tool_name": tool_name,
        "ok": ok,
        "duration_ms": ms,
        "session_id": session_id,
        "task_id": None if task_id is None else str(task_id),
        "model": model,
        "record_mode": settings["record_mode"],
    }
    if settings["record_mode"] == "full":
        safe_args = json_safe(redact(args, settings["redact_keys"]))
        args_bytes = _json_bytes(safe_args)
        if len(args_bytes) > MAX_ARGS_BYTES:
            text, _ = truncate_text(args_bytes.decode("utf-8"), MAX_ARGS_BYTES)
            payload["args"] = text
            payload["args_truncated"] = True
        else:
            payload["args"] = safe_args
        raw, cut = truncate_text(result_text(result), settings["max_result_bytes"])
        payload["result_raw"] = raw
        payload["result_truncated"] = cut

    # Belt and braces: never exceed the payload limit, whatever the inputs.
    while len(_json_bytes(payload)) > MAX_PAYLOAD_BYTES and payload.get("result_raw"):
        cut_to = max(0, len(payload["result_raw"].encode("utf-8")) // 2)
        payload["result_raw"], _ = truncate_text(payload["result_raw"], cut_to)
        payload["result_truncated"] = True
    if len(_json_bytes(payload)) > MAX_PAYLOAD_BYTES:
        payload.pop("args", None)
        payload.pop("result_raw", None)
        payload["dropped_for_size"] = True

    header: dict[str, Any] = {"tags": list(TOOL_TAGS)}
    peers = peers_for(tool_name, settings["peer_map"])
    if peers:
        header["peers"] = peers
    return summary, payload, header


# ---------------------------------------------------------------- chain start

def save_head(path: str | Path | None, seq: int, last_hash: str) -> None:
    """Remember the last acknowledged (seq, hash) so an offline start can continue the chain."""
    if path is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"seq": seq, "last_hash": last_hash}))
    os.replace(tmp, p)


def load_head(path: str | Path | None) -> tuple[int, str] | None:
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text())
        return int(data["seq"]), str(data["last_hash"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def start_chain(client: LTMSClient, outbox: Outbox | None, head_path: str | Path | None) -> str:
    """Put the client at the end of its chain. Returns "online" or "offline".

    Online: sync the head with LTMS, then reload records still waiting in the
    persistent outbox. Offline: continue from the saved head plus the waiting
    records, so new records chain onto them. Raises if neither is possible
    (offline and no saved head): signing then would fork the chain.
    """
    try:
        client.sync_head()
        mode = "online"
    except (LTMSError, httpx.HTTPError, OSError):
        head = load_head(head_path)
        if head is None:
            raise
        client.seq, client.last_hash = head
        client.outbox = []
        mode = "offline"
    if outbox is not None:
        for rec in outbox.pending(client.name, 10_000):
            if rec["seq"] > client.seq:
                client.outbox.append(rec)
        outbox.prune(client.name, client.seq)
    if mode == "online":
        save_head(head_path, client.seq, client.last_hash)
    return mode


# ---------------------------------------------------------------- the recorder

class Recorder:
    """Turns Hermes hook payloads into signed records and sends them.

    Hook methods are fast and never touch the network: they build the record's
    content, stamp the time, and put it on an in-memory queue. ``flush`` signs
    what is queued (continuing the member's chain), stores it in the persistent
    outbox, and sends it. With ``background=True`` a daemon thread flushes every
    ``flush_interval_seconds``, or sooner once ``flush_batch`` records wait.
    """

    START_RETRY_SECONDS = 30.0

    def __init__(
        self,
        client: LTMSClient,
        *,
        settings: dict | None = None,
        outbox: Outbox | None = None,
        head_path: str | Path | None = None,
        background: bool = False,
    ) -> None:
        self.client = client
        self.outbox = outbox
        self.head_path = head_path
        self.settings = resolve_settings(settings)
        self.ready = False
        self.dropped = 0  # oldest unsigned events dropped because the queue was full
        self._next_start = 0.0
        self._pending: deque[tuple[str, str, dict, dict, str]] = deque()
        self._state_lock = threading.Lock()   # session state and the pending queue: held briefly
        self._send_lock = threading.Lock()    # the client: held while signing and sending
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.session_id: str | None = None
        self.model: str | None = None
        self.calls = 0
        self.errors = 0
        self._thread: threading.Thread | None = None
        if background:
            self._thread = threading.Thread(target=self._run, name="ltms-recorder", daemon=True)
            self._thread.start()

    # -------------------------------------------------------------- hooks
    def on_session_start(self, session_id: Any = None, model: Any = None, platform: Any = None, **_: Any) -> None:
        try:
            with self._state_lock:
                self.session_id = None if session_id is None else str(session_id)
                self.model = None if model is None else str(model)
                self.calls = self.errors = 0
                self._enqueue(
                    "event",
                    "Hermes session started",
                    {"session_id": self.session_id, "model": self.model,
                     "platform": None if platform is None else str(platform)},
                    {"tags": list(SESSION_TAGS)},
                )
        except Exception as exc:  # noqa: BLE001 - never break Hermes
            log.warning("ltms_recorder: session start not recorded (%s)", type(exc).__name__)

    def on_post_tool_call(self, tool_name: Any = None, args: Any = None, result: Any = None,
                          task_id: Any = None, duration_ms: Any = None, **_: Any) -> None:
        try:
            with self._state_lock:
                if self.settings["record_mode"] == "off":
                    self.calls += 1
                    self.errors += int(looks_like_error(result))
                    return
                summary, payload, header = tool_call_record(
                    tool_name=str(tool_name or "unknown"), args=args, result=result,
                    task_id=task_id, duration_ms=duration_ms, session_id=self.session_id,
                    model=self.model, settings=self.settings,
                )
                self.calls += 1
                self.errors += int(not payload["ok"])
                self._enqueue("action", summary, payload, header)
        except Exception as exc:  # noqa: BLE001 - never break Hermes
            log.warning("ltms_recorder: tool call not recorded (%s)", type(exc).__name__)

    def on_session_end(self, session_id: Any = None, completed: Any = None, interrupted: Any = None,
                       model: Any = None, platform: Any = None, **_: Any) -> None:
        try:
            with self._state_lock:
                sid = None if session_id is None else str(session_id)
                state = "interrupted" if interrupted else ("completed" if completed else "ended")
                self._enqueue(
                    "event",
                    f"Hermes session {state}: {self.calls} tool calls, {self.errors} errors",
                    {"session_id": sid, "completed": bool(completed), "interrupted": bool(interrupted),
                     "model": None if model is None else str(model),
                     "platform": None if platform is None else str(platform),
                     "tool_calls": self.calls, "tool_errors": self.errors},
                    {"tags": list(SESSION_TAGS)},
                )
                if sid is None or sid == self.session_id:
                    self.session_id = None
            if self._thread is None:
                self.flush()
            else:
                # Sign now if the chain is ready (local work only) so the records
                # survive in the persistent outbox even if Hermes exits next.
                self.sign_pending(allow_network=False)
                self._wake.set()
        except Exception as exc:  # noqa: BLE001 - never break Hermes
            log.warning("ltms_recorder: session end not recorded (%s)", type(exc).__name__)

    def _enqueue(self, type_: str, summary: str, payload: dict, header: dict) -> None:
        if len(self._pending) >= MAX_PENDING:
            self._pending.popleft()
            self.dropped += 1
        self._pending.append((type_, clip_summary(summary), payload, header, now_ts()))
        if len(self._pending) >= self.settings["flush_batch"]:
            self._wake.set()

    # -------------------------------------------------------------- signing and sending
    def _try_start(self, allow_network: bool = True) -> bool:
        if self.ready:
            return True
        if not allow_network or time.monotonic() < self._next_start:
            return False
        try:
            mode = start_chain(self.client, self.outbox, self.head_path)
            self.ready = True
            log.info("ltms_recorder: chain ready (%s, seq %d)", mode, self.client.seq)
        except Exception as exc:  # noqa: BLE001
            self._next_start = time.monotonic() + self.START_RETRY_SECONDS
            log.info("ltms_recorder: not ready yet (%s)", type(exc).__name__)
        return self.ready

    def sign_pending(self, allow_network: bool = True) -> int:
        """Sign queued hook events onto the chain and persist them. Returns how many."""
        with self._send_lock:
            # Start the chain even with nothing new queued: records left in the
            # persistent outbox by an earlier run are restored and sent.
            if not self._try_start(allow_network) or not self._pending:
                return 0
            signed = 0
            while self._pending:
                type_, summary, payload, header, ts = self._pending.popleft()
                try:
                    rec = self.client.append(type_, payload=json_safe(payload), summary=summary,
                                             ts=ts, **header)
                except Exception as exc:  # noqa: BLE001 - one bad event must not block the rest
                    self.dropped += 1
                    log.warning("ltms_recorder: event not signed (%s)", type(exc).__name__)
                    continue
                if self.outbox is not None:
                    self.outbox.add(rec)
                signed += 1
            return signed

    @property
    def queued(self) -> int:
        """Events waiting: not yet signed, plus signed but not yet acknowledged."""
        return len(self._pending) + len(self.client.outbox)

    def flush(self) -> int:
        """Sign and send. Returns how many records LTMS acknowledged. Never raises.

        * A record LTMS rejects (and everything signed after it, whose chain
          depends on it) is dropped, and the client re-syncs its head.
        * Anything else (offline, rate limited, server error) leaves records
          queued for the next flush.
        """
        try:
            self.sign_pending()
        except Exception as exc:  # noqa: BLE001
            log.warning("ltms_recorder: signing failed (%s)", type(exc).__name__)
        with self._send_lock:
            if not self.ready or not self.client.outbox:
                return 0
            before = self.client.seq
            try:
                self.client.flush()
            except LTMSError as exc:
                if exc.status == 422 and self.client.outbox:
                    rejected = min(r["seq"] for r in self.client.outbox)
                    dropped = len(self.client.outbox)
                    self.client.outbox = []
                    if self.outbox is not None:
                        self.outbox.conn.execute(
                            "DELETE FROM outbox WHERE agent = ? AND seq >= ?", (self.client.name, rejected))
                        self.outbox.conn.commit()
                    log.warning("ltms_recorder: LTMS rejected seq %d (%s); dropped %d queued records",
                                rejected, exc.code, dropped)
                    try:
                        self.client.sync_head()
                        save_head(self.head_path, self.client.seq, self.client.last_hash)
                    except Exception:  # noqa: BLE001
                        self.ready = False  # re-sync before signing again
                else:
                    log.info("ltms_recorder: send deferred (%s %s)", exc.status, exc.code)
            except httpx.HTTPError as exc:
                log.info("ltms_recorder: LTMS unreachable, records kept (%s)", type(exc).__name__)
            except Exception as exc:  # noqa: BLE001
                log.warning("ltms_recorder: send failed (%s)", type(exc).__name__)
            if self.outbox is not None:
                self.outbox.prune(self.client.name, self.client.seq)
            if self.client.seq != before:
                try:
                    save_head(self.head_path, self.client.seq, self.client.last_hash)
                except OSError as exc:
                    log.warning("ltms_recorder: head not saved (%s)", type(exc).__name__)
            return max(0, self.client.seq - before)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.settings["flush_interval_seconds"])
            self._wake.clear()
            self.flush()

    def close(self) -> None:
        """Stop the background thread and make a last attempt to send."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.flush()
