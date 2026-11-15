"""Episodes, pulses, series and heartbeat for one member or swarm (#401).

Derived from records **at read time**: nothing new is stored, old data works,
and the derivation can improve later. ``GET /api/admin/profile/episodes``
serves it to the Chronograph, the System and Sky views and their table twins.

- **pulses**: one per record in range, in time order:
  ``[t, transit_s, type, category, source, summary]`` with ``t`` in seconds
  from ``since`` and ``transit_s = received_at - ts``.
- **lanes**: ``mind``, ``voice``, ``attention``, ``body``, ``motion``,
  ``power``, ``behavior`` (plus ``animating`` and ``wheels`` when a member
  sends those episodes, #408 section 6). Each episode is
  ``[start, end, state, detail]``, with an optional fifth element, an object
  of flags: ``{"open": true}`` (not closed inside the range) or
  ``{"estimated": true}`` (length guessed, e.g. a reply's speaking time).
- **series**: ``volts`` and ``wheel_mmps`` as ``[t, value]`` per body window.
- **heartbeat**: ``interval_s`` (median of the last 5 self-report intervals)
  and ``last_t``.

Robot-specific knowledge lives in the tables below (``MAPPING``, ``STATES``,
``SOURCE_OF``); other robots plug in by tags, not code paths. A record no rule
matches is a plain pulse (its type's category, source ``hub``) on no lane.

Two tagging schemes are understood:

- before #408: body windows are perceptions tagged ``internal, telemetry,
  body`` with ``{"kind": "body", "values": {name: {min, max, last}}}``; flag
  changes are events tagged ``state-change`` with summary ``"<flag> on|off"``.
- #408: episodes are events tagged ``episode-start`` / ``episode-end`` plus
  the state; the end ``refs`` the start and carries ``duration_s``. Health is
  a self-report tagged ``body-health``; sensed groups are perceptions tagged
  ``telemetry, body`` with ``{"kind": "sensor", "sensor": "wheels"|...}``.

Payloads are read only where the header can't say it: body telemetry (wheel
speeds, volts), face perceptions (the name), LLM replies (the full text; the
summary is cut) and episode ends (``duration_s``, when the start is out of
range). At most ``MAX_RECORDS`` records are read per call, oldest first;
beyond that ``truncated`` is true.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Record, RecordPayload
from server.services.errors import ServiceError
from server.services.profile import CATEGORY_OF, Subject

MAX_RECORDS = 20_000
DEFAULT_SPAN = timedelta(hours=24)
MAX_SPAN = timedelta(days=366)
SUMMARY_MAX = 80
PAYLOAD_CHUNK = 2_000

LANES = ("mind", "voice", "attention", "body", "motion", "power", "behavior")
OPTIONAL_LANES = ("animating", "wheels")  # #408 section 6: only when present

# --------------------------------------------------------------------------- #
# Mapping table: first rule whose type matches (None = any) and whose tags are
# all on the record wins. ``kind`` picks the derivation below.
# --------------------------------------------------------------------------- #
MAPPING: list[tuple[str | None, frozenset, str]] = [
    ("event", frozenset({"episode-start"}), "episode_start"),        # #408
    ("event", frozenset({"episode-end"}), "episode_end"),            # #408
    ("perception", frozenset({"telemetry", "body-health"}), "health"),  # #408 self-report
    ("perception", frozenset({"telemetry", "body"}), "body"),        # body window or #408 sensor group
    ("perception", frozenset({"telemetry", "pose"}), "pose"),        # #407
    ("event", frozenset({"state-change"}), "flag"),                  # before #408
    ("perception", frozenset({"face"}), "face"),
    ("event", frozenset({"wake-word"}), "wake"),
    ("perception", frozenset({"heard"}), "heard"),
    (None, frozenset({"user-intent"}), "intent"),
    (None, frozenset({"custom-intent"}), "intent"),
    ("event", frozenset({"llm-request"}), "llm_request"),
    ("communication", frozenset({"reply"}), "llm_reply"),
    ("perception", frozenset({"telemetry", "internal"}), "self"),    # heartbeats, health
]

# The part that made a record, by kind. ``flag`` and episodes use STATES.
SOURCE_OF = {
    "health": "battery", "body": "imu", "pose": "imu", "face": "camera",
    "wake": "mic", "heard": "mic", "intent": "mic",
    "llm_request": "stt", "llm_reply": "llm", "self": "battery",
}

# Body states: a flag name (before #408, from "<flag> on|off") or a state tag
# (#408) -> (lane, state, source). Synonyms share a state and form one episode.
STATES: dict[str, tuple[str | None, str, str]] = {
    "picked up": ("body", "held", "imu"),
    "being held": ("body", "held", "imu"),
    "held": ("body", "held", "imu"),
    "cliff detected": ("body", "cliff", "cliff"),
    "cliff": ("body", "cliff", "cliff"),
    "falling": ("body", "falling", "imu"),
    "button pressed": ("body", "button", "imu"),
    "button": ("body", "button", "imu"),
    "on charger": ("power", "charger", "battery"),
    "on-charger": ("power", "charger", "battery"),
    "charging": (None, "charging", "battery"),  # inside a charger episode; pulse only
    "animating": ("animating", "animating", "imu"),
    "wheels-moving": ("wheels", "moving", "imu"),
    "wheels moving": ("wheels", "moving", "imu"),
}
STATE_DETAIL = {"held": "held", "cliff": "cliff", "falling": "falling", "button": "button",
                "charger": "on charger", "animating": "animating", "moving": "wheels moving"}

BODY_WINDOW_S = 30.0
MOTION = ((5, "still"), (40, "nudging"))  # max wheel speed <= limit -> state; above -> driving
FACE_S = 10.0
WAKE_S = 3.0
HEARD_S = 2.0
REPLY_CHARS_PER_S = 15.0
HEARTBEAT_INTERVALS = 5
PAYLOAD_KINDS = {"body", "health", "face", "llm_reply", "episode_end"}


def classify(type_: str, tags) -> str | None:
    tagset = set(tags or [])
    for rtype, need, kind in MAPPING:
        if (rtype is None or rtype == type_) and need <= tagset:
            return kind
    return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_range(since: str | None, until: str | None) -> tuple[datetime, datetime]:
    def parse(name, value):
        try:
            return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except (AttributeError, ValueError):
            raise ServiceError(422, "bad_range", f"{name} must be an ISO 8601 time") from None
    end = parse("until", until) if until else datetime.now(timezone.utc)
    start = parse("since", since) if since else end - DEFAULT_SPAN
    if start >= end:
        raise ServiceError(422, "bad_range", "since must be before until")
    if end - start > MAX_SPAN:
        raise ServiceError(422, "bad_range", "the range may span at most 366 days")
    return start, end


def _ref_values(refs) -> list[str]:
    """``"agent:seq"`` strings from refs (an object per the contract; a list is tolerated)."""
    values = refs.values() if isinstance(refs, dict) else refs if isinstance(refs, list) else []
    out = []
    for v in values:
        out.extend(x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str))
    return out


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _numbers(v) -> list[float]:
    """Every number in a value, a list or an object (one level of nesting each)."""
    if _num(v) is not None:
        return [_num(v)]
    if isinstance(v, dict):
        return [x for item in v.values() for x in _numbers(item)]
    if isinstance(v, list):
        return [x for item in v for x in _numbers(item)]
    return []


def _wheel_speed(body: dict) -> float | None:
    """Max |wheel speed| in mm/s in a body window or a #408 ``wheels`` group."""
    values = body.get("values")
    if isinstance(values, dict):
        found = [abs(x) for k, v in values.items() if "wheel_speed" in k for x in _numbers(v)]
        if found:
            return max(found)
    if body.get("sensor") == "wheels":
        found = [abs(x) for x in _numbers(body.get("stats"))]
        found += [abs(x) for s in body.get("samples") or [] if isinstance(s, dict) for x in _numbers(s.get("v"))]
        if found:
            return max(found)
    return None


def _volts(body: dict) -> float | None:
    """Battery volts in a body window or a #408 body-health report."""
    for holder in (body.get("values"), body.get("stats"), body):
        if isinstance(holder, dict) and "battery_volts" in holder:
            v = holder["battery_volts"]
            if isinstance(v, dict):
                v = v.get("last", v.get("mean"))
            if _num(v) is not None:
                return _num(v)
    return None


class _Builder:
    def __init__(self, since: datetime, until: datetime):
        self.since, self.end = since, round((until - since).total_seconds(), 2)
        self.lanes: dict[str, list] = {k: [] for k in LANES + OPTIONAL_LANES}
        self.series = {"volts": [], "wheel_mmps": []}
        self.flags_on: dict[tuple, dict] = {}       # (agent, state) -> {"t": start, "raw": {flags}}
        self.starts: dict[str, tuple] = {}          # "agent:seq" of an open #408 start -> (agent, state)
        self.pending_llm: dict[str, list] = {}      # agent -> [(t, "agent:seq")]
        self.self_reports: list[float] = []
        self.power_seen = False

    def t(self, ts: datetime) -> float:
        return round((_aware(ts) - self.since).total_seconds(), 2)

    def add(self, lane, start, end, state, detail, **flags):
        start, end = max(0.0, start), min(self.end, end)
        ep = [round(start, 2), round(max(end, start), 2), state, detail]
        if flags:
            ep.append(flags)
        self.lanes[lane].append(ep)

    # -- body states ---------------------------------------------------------
    def state_on(self, agent, name, t, seq_key=None):
        lane, state, _ = STATES[name]
        if lane is None:
            return
        key = (agent, state)
        cur = self.flags_on.setdefault(key, {"t": t, "raw": set()})
        cur["raw"].add(name)
        if seq_key:
            self.starts[seq_key] = key

    def state_off(self, agent, name, t, *, all_raw=False, duration=None):
        lane, state, _ = STATES[name]
        if lane is None:
            return
        cur = self.flags_on.get((agent, state))
        if cur is None:  # began before the range
            start = t - duration if duration is not None else 0.0
            self.add(lane, start, t, state, STATE_DETAIL.get(state, state))
            return
        cur["raw"].discard(name)
        if all_raw or not cur["raw"]:
            del self.flags_on[(agent, state)]
            self.add(lane, cur["t"], t, state, STATE_DETAIL.get(state, state))

    def record(self, r, kind: str | None, body: dict | None):
        t = self.t(r.ts)
        tags = r.tags or []
        if kind == "flag":
            name, _, onoff = (r.summary or "").rpartition(" ")
            if name in STATES and onoff in ("on", "off"):
                self.power_seen |= STATES[name][0] == "power"
                if onoff == "on":
                    self.state_on(r.agent, name, t)
                else:
                    self.state_off(r.agent, name, t)
        elif kind in ("episode_start", "episode_end"):
            name = next((x for x in tags if x in STATES), None)
            if kind == "episode_start" and name is None:
                return
            self.power_seen |= name is not None and STATES[name][0] == "power"
            if kind == "episode_start":
                self.state_on(r.agent, name, t, f"{r.agent}:{r.seq}")
            else:
                key = next((self.starts.pop(v) for v in _ref_values(r.refs) if v in self.starts), None)
                if key is not None and name is None:
                    name = next(n for n in STATES if STATES[n][1] == key[1])
                if name is not None:
                    dur = _num((body or {}).get("duration_s"))
                    self.state_off(r.agent, name, t, all_raw=True, duration=dur)
        elif kind in ("body", "health") and body is not None:
            volts = _volts(body)
            if volts is not None:
                self.power_seen = True
                self.series["volts"].append([t, round(volts, 3)])
            speed = _wheel_speed(body) if kind == "body" else None
            if speed is not None:
                window = _num(body.get("window_s")) or BODY_WINDOW_S
                state = next((s for limit, s in MOTION if speed <= limit), "driving")
                self.add("motion", t - window, t, state, f"{round(speed)} mm/s max")
                self.series["wheel_mmps"].append([t, round(speed, 1)])
        elif kind == "face":
            name = (body or {}).get("name")
            known = isinstance(name, str) and name and not name.startswith("face #") and name != "unknown"
            if known:
                self.add("attention", t, t + FACE_S, "known", name)
            else:
                fid = (body or {}).get("face_id")
                detail = f"face #{fid}" if isinstance(fid, int) and fid > 0 else "unknown"
                self.add("attention", t, t + FACE_S, "unknown", detail)
        elif kind == "wake":
            self.add("voice", t, t + WAKE_S, "listening", "wake word")
        elif kind == "heard":
            s = r.summary or ""
            self.add("voice", t, t + HEARD_S, "heard", s[len("heard: "):] if s.startswith("heard: ") else s)
        elif kind == "llm_request":
            self.pending_llm.setdefault(r.agent, []).append((t, f"{r.agent}:{r.seq}"))
        elif kind == "llm_reply":
            pending = self.pending_llm.get(r.agent) or []
            refs = set(_ref_values(r.refs))
            match = next((p for p in pending if p[1] in refs), pending[-1] if pending else None)
            if match is not None:
                self.add("mind", match[0], t, "thinking", "LLM request")
                pending.remove(match)
            text = (body or {}).get("text")
            text = text if isinstance(text, str) and text else (r.summary or "")
            self.add("voice", t, t + len(text) / REPLY_CHARS_PER_S, "replying",
                     text[:SUMMARY_MAX], estimated=True)
        if r.type == "perception" and {"telemetry", "internal"} <= set(tags):
            self.self_reports.append(t)

    def finish(self) -> dict:
        for (agent, state), cur in self.flags_on.items():
            lane = next(v[0] for v in STATES.values() if v[1] == state)
            self.add(lane, cur["t"], self.end, state, STATE_DETAIL.get(state, state), open=True)
        for pending in self.pending_llm.values():
            if pending:  # the newest unanswered request is still thinking
                self.add("mind", pending[-1][0], self.end, "thinking", "LLM request", open=True)
        if self.power_seen:  # gaps between charger episodes are on battery
            prev = 0.0
            for a, b, *_ in sorted(e for e in self.lanes["power"] if e[2] == "charger"):
                if a > prev:
                    self.add("power", prev, a, "battery", "on battery")
                prev = max(prev, b)
            if prev < self.end:
                self.add("power", prev, self.end, "battery", "on battery")
        lanes = {k: sorted(v, key=lambda e: (e[0], e[1])) for k, v in self.lanes.items()
                 if k in LANES or v}
        sr = self.self_reports
        gaps = [b - a for a, b in zip(sr, sr[1:])][-HEARTBEAT_INTERVALS:]
        heartbeat = {"interval_s": round(statistics.median(gaps), 2) if gaps else None,
                     "last_t": sr[-1] if sr else None}
        return {"lanes": lanes, "series": self.series, "heartbeat": heartbeat}


def _source(kind: str | None, r) -> str:
    if kind == "flag":
        name = (r.summary or "").rpartition(" ")[0]
        return STATES[name][2] if name in STATES else "imu"
    if kind in ("episode_start", "episode_end"):
        name = next((x for x in r.tags or [] if x in STATES), None)
        return STATES[name][2] if name else "imu"
    return SOURCE_OF.get(kind, "hub")


def _category(r) -> str | None:
    tags = r.tags or []
    if r.type == "perception" and "telemetry" in tags and "internal" in tags:
        return "self"
    return CATEGORY_OF.get(r.type)  # `system` has none


async def _payloads(session: AsyncSession, ids: list[int]) -> dict[int, dict]:
    out = {}
    for i in range(0, len(ids), PAYLOAD_CHUNK):
        rows = await session.execute(select(RecordPayload.record_id, RecordPayload.body)
                                     .where(RecordPayload.record_id.in_(ids[i:i + PAYLOAD_CHUNK])))
        out.update({rid: body for rid, body in rows.all() if isinstance(body, dict)})
    return out


async def episodes(session: AsyncSession, subject: Subject, since: datetime, until: datetime) -> dict:
    where = and_(subject.records(), Record.ts >= since, Record.ts < until)
    rows = (await session.execute(
        select(Record.id, Record.agent, Record.seq, Record.ts, Record.received_at, Record.type,
               Record.summary, Record.tags, Record.refs)
        .where(where).order_by(Record.ts, Record.id).limit(MAX_RECORDS + 1))).all()
    truncated = len(rows) > MAX_RECORDS
    rows = rows[:MAX_RECORDS]
    kinds = [classify(r.type, r.tags) for r in rows]
    bodies = await _payloads(session, [r.id for r, k in zip(rows, kinds) if k in PAYLOAD_KINDS])

    b = _Builder(since, until)
    pulses = []
    for r, kind in zip(rows, kinds):
        t = b.t(r.ts)
        transit = round((_aware(r.received_at) - _aware(r.ts)).total_seconds(), 2)
        pulses.append([t, transit, r.type, _category(r), _source(kind, r), (r.summary or "")[:SUMMARY_MAX]])
        b.record(r, kind, bodies.get(r.id))
    out = b.finish()
    return {"subject": subject.as_dict(), "since": since.isoformat(), "until": until.isoformat(),
            "duration_s": b.end, "count": len(rows), "truncated": truncated, "pulses": pulses, **out}
