"""Events and body state from a Vector robot through its Python SDK (#312, #407, #408).

Uses ``wirepod_vector_sdk`` (the ``anki_vector`` package, version 0.8.1 read
for this adapter), connected to a robot served by wire-pod. Event names and
fields come from that package: ``anki_vector/events.py`` (``Events``),
``anki_vector/status.py`` (``RobotStatus``), ``anki_vector/touch.py``,
``anki_vector/proximity.py``, ``anki_vector/animation.py``,
``anki_vector/robot.py`` (``get_battery_state``, ``get_version_state``).
Everything outside the event stream is read with ``getattr`` and skipped when
absent, so an SDK without it records less, never a made-up value.

The adapter **watches**, with one exception: the energy reflex (#513, below and
``adapters/vector_energy.py``). Apart from that it never requests behavior
control, so Vector behaves as Vector. Configure the SDK the usual way first
(``python -m anki_vector.configure``); this adapter needs no secrets of its own.

Records (one per event):

| SDK | Record |
|---|---|
| ``wake_word`` | ``event`` "wake word", tags voice,wake-word |
| ``user_intent`` | ``event`` "user intent: <intent>", tags voice,user-intent; the SDK's ``intent_data`` in the payload |
| ``robot_observed_face`` | ``perception`` "face seen", tags vision,face (face id, name if enrolled, expression); at most one per face per ``VECTOR_FACE_GAP_SECONDS`` (default 10) |
| a body state starting / ending (below) | ``event`` "<state> started" / "<state> ended", tags body, ``episode-start`` / ``episode-end``, ``<state>`` |

**Episodes (#408).** Status flags are watched every ``VECTOR_FLAG_SECONDS``
(default 0.1) and become one start and one end record per episode, not one per
flag flip. Synonymous flags are one state; the payload keeps the SDK's raw flag
names (``flags``), so nothing is hidden:

| State | SDK flags |
|---|---|
| ``held`` | ``RobotStatus.is_picked_up``, ``is_being_held`` |
| ``cliff`` | ``is_cliff_detected`` |
| ``falling`` | ``is_falling`` |
| ``on-charger`` | ``is_on_charger`` |
| ``charging`` | ``is_charging`` |
| ``button`` | ``is_button_pressed`` |
| ``touch`` | ``touch.last_sensor_reading.is_being_touched`` (a stroke is one episode) |
| ``animating`` | ``is_animating``: an animation is playing. The SDK doesn't say **which** animation, sound or light pattern, so neither does the record |
| ``wheels-moving`` | ``are_wheels_moving`` |

The end record refs the start (``refs: {"start": "<agent>:<seq>"}``) and carries
``duration_s``. Each record's ``ts`` is when the state really changed. A state
that flips back within ``VECTOR_DEBOUNCE_SECONDS`` (0.5) is no episode at all;
it's counted in the next body window's ``stats.flicker``. A state already on when
the adapter connects has no start record, so its end has no refs or duration.

**Pose (#407):** ``robot.pose`` x/y/z (mm) and ``rotation.angle_z`` (rad), with the
frame ``vector-odom:<origin_id>`` (``anki_vector.util.Pose.origin_id``, read
with ``getattr``; plain ``vector-odom`` if absent). The sidecar signs it as a
sensed ``telemetry,pose`` record (``kind: pose``).

**Sensor groups (#408)**, sampled every ``VECTOR_BODY_SECONDS`` (default 1); the
sidecar signs one sensed ``telemetry,body`` record per group per window:

| Group | Modality | Unit | Channels (SDK) |
|---|---|---|---|
| ``wheels`` | encoder | mm/s | left, right (``left_wheel_speed_mmps``, ``right_wheel_speed_mmps``) |
| ``imu`` | imu | (not stated by the SDK) | accel_x/y/z, gyro_x/y/z (``robot.accel``, ``robot.gyro``) |
| ``head`` | angle | rad | ``head_angle_rad`` |
| ``lift`` | height | mm | ``lift_height_mm`` |
| ``touch`` | force | (raw) | raw, pressed (``touch.last_sensor_reading``) |
| ``proximity`` | range | mm | distance, signal_quality, unobstructed, found_object, lift_in_fov (``proximity.last_sensor_reading``; present in 0.8.1) |

The SDK's docstrings don't give the accelerometer's or gyro's units, so the imu
record has ``unit: null`` rather than a guess.

**Health (#408)**, every ``VECTOR_BATTERY_SECONDS`` (default 30), from
``get_battery_state()``: battery volts and level, ``is_charging``,
``is_on_charger_platform``. The sidecar folds these into its ``internal``
body-health self-report. 0.8.1 reports no temperature, so none is recorded.

**Specs (#408).** On connecting, the adapter reads ``robot.anim.anim_list`` and
``robot.anim.anim_trigger_list`` once (0.8.1's name; ``anim_list_triggers`` is
tried too) and reports their **counts**, never the lists, with the SDK version
(``anki_vector.__version__``) and firmware (``get_version_state().os_version``),
through ``on_specs`` (the sidecar sets it to ``report_specs``).

Not available from the SDK, so not recorded: Vector's stimulation level, which
animation is playing.

**Stale stream (#486).** The SDK connection can stop delivering new state without
raising anything: on 23 Nov 2026 it froze after another SDK client (VectorClaw)
drove Vector off the charger. The sidecar kept sampling the last values, the
deadbands saw "no change", and 20 minutes of exploring looked like rest. A live
robot never reports *bit-identical* IMU, touch ``raw`` and proximity
``signal_quality`` for long; those channels jitter even at rest. When all of
them stay exactly the same for ``VECTOR_STALE_SECONDS`` (default 10; 0 = off), the
adapter signs one ``body stream stale`` event, drops the connection and reconnects.
A battery reading older than three polls no longer counts as "on the charger", so
a stale stream can't keep the 30-minute resting heartbeat.

**Renewing a stale stream (#498).** A reconnect alone proves nothing: the new
stream can be frozen too (Vector asleep or low on battery), and on 23–24 Nov a
frozen stream sent the same readings for 23 hours. So after a stale stream:

- no body or pose readings are signed until a live channel actually **changes** on
  the new connection; only then is ``body stream resumed`` signed (``outage_s``,
  ``reconnects``). Until then the record shows silence, not old values;
- if the new stream stays bit-identical for ``VECTOR_STALE_SECONDS`` too, the
  adapter reconnects again, waiting 2 s, then doubling up to
  ``VECTOR_STALE_RETRY_MAX_SECONDS`` (default 30). No extra stale records;
- the battery poll is a direct request, not part of the stream, so it stays live.
  While stale, any change in battery level, charging or charger (Vector woke up,
  was docked or picked up) reconnects at once and resets the backoff to 2 s.

Battery health keeps signing throughout, since it is read live.

**Energy reflex (#513): the sidecar's only command.** Vector doesn't go home on his
own, and level 1 comes only minutes before he powers off. After
``VECTOR_HOME_AFTER_MINUTES`` (default 45; ``0`` turns it off) off the dock, by the
battery poll's charger flag, or at battery level 1, the adapter takes behavior
control, calls ``drive_on_charger`` and releases control; once more if he's still
off the dock at the next poll; then Vector says "I need my charger." and a swarm
question is signed. Never while he's held or falling. Every step is a record:
``body,energy`` events, ``action`` records for each command with its outcome, and
an ``energy-outcome`` event when he's docked again (``docked``, ``rescued``,
``powered-off``, ``not-needed``). Details in ``adapters/vector_energy.py``.

Settings: VECTOR_SERIAL (optional; the SDK's default robot otherwise),
VECTOR_BODY_SECONDS, VECTOR_FLAG_SECONDS, VECTOR_DEBOUNCE_SECONDS,
VECTOR_FACE_GAP_SECONDS, VECTOR_BATTERY_SECONDS, VECTOR_STALE_SECONDS,
VECTOR_STALE_RETRY_MAX_SECONDS, VECTOR_HOME_AFTER_MINUTES.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from adapters.base import Adapter, Emit, Item
from adapters.vector_energy import (
    ASK_HELP,
    GO_HOME,
    HELP_TEXT,
    HOME_AFTER_SECONDS,
    EnergyReflex,
)

# state -> the SDK's RobotStatus flags for it (any on: the state is on)
STATES = {
    "held": ("is_picked_up", "is_being_held"),
    "cliff": ("is_cliff_detected",),
    "falling": ("is_falling",),
    "on-charger": ("is_on_charger",),
    "charging": ("is_charging",),
    "button": ("is_button_pressed",),
    "touch": ("is_touched",),
    "animating": ("is_animating",),
    "wheels-moving": ("are_wheels_moving",),
}
STATUS_FLAGS = tuple(f for flags in STATES.values() for f in flags if f != "is_touched")
DEBOUNCE_SECONDS = 0.5
STALE_SECONDS = 10.0
STALE_RETRY_SECONDS = 2.0       # first reconnect after a frozen stream
STALE_RETRY_MAX_SECONDS = 30.0  # backoff cap while it stays frozen
# Channels that jitter on a live robot even at rest (#448, #486): bit-identical for
# STALE_SECONDS means the SDK stopped delivering state.
LIVE_CHANNELS = {"imu": ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z"),
                 "touch": ("raw",), "proximity": ("signal_quality",)}


class StaleStream(Exception):
    """The SDK stream stopped changing; reconnect (#486)."""


class StreamWatch:
    """Tells a frozen SDK stream from a still robot (#486).

    ``update(groups, now)`` takes one ``sensor_values`` reading. It returns the
    seconds the live channels have been bit-identical, once that reaches
    ``stale_seconds`` (else None). With no live channels to compare it never
    calls a stream stale.
    """

    def __init__(self, stale_seconds: float = STALE_SECONDS):
        self.stale_seconds = stale_seconds
        self.last: tuple | None = None
        self.since: float | None = None

    @staticmethod
    def fingerprint(groups: dict[str, dict]) -> tuple | None:
        vals = tuple((g, c, groups[g][c]) for g, cs in LIVE_CHANNELS.items() if g in groups
                     for c in cs if c in groups[g])
        return vals or None

    def reset(self) -> None:
        self.last = self.since = None

    def update(self, groups: dict[str, dict], now: float) -> float | None:
        if self.stale_seconds <= 0:
            return None
        fp = self.fingerprint(groups)
        if fp is None:
            return None
        if fp != self.last:
            self.last, self.since = fp, now
            return None
        frozen = now - (self.since if self.since is not None else now)
        return frozen if frozen >= self.stale_seconds else None


class EpisodeTracker:
    """Turns successive flag readings into episodes: merged synonyms, debounced. Pure: easy to test.

    ``update(flags, now)`` returns ``(events, flicker)``: events are
    ``("start", state, at, flags)`` or ``("end", state, at, flags, started_at)``
    (``started_at`` is None when the state was already on at the first reading);
    ``flicker`` is ``{state: n}`` for flips shorter than ``debounce``.
    """

    def __init__(self, debounce: float = DEBOUNCE_SECONDS):
        self.debounce = debounce
        self.on: dict[str, bool] = {}
        self.since: dict[str, float | None] = {}
        self.pending: dict[str, tuple[bool, float, list[str]]] = {}
        self.seen: dict[str, set[str]] = {}

    def update(self, flags: dict[str, bool], now: float) -> tuple[list[tuple], dict[str, int]]:
        events, flicker = [], {}
        for state, names in STATES.items():
            present = [f for f in names if f in flags]
            if not present:
                continue
            raw_on = [f for f in present if flags[f]]
            raw = bool(raw_on)
            if state not in self.on:  # first reading: the current state, no event
                self.on[state], self.since[state] = raw, None
                self.seen[state] = set(raw_on)
                continue
            if self.on[state]:
                self.seen[state].update(raw_on)
            if raw == self.on[state]:
                if state in self.pending:  # flipped back within the debounce: no episode
                    del self.pending[state]
                    flicker[state] = flicker.get(state, 0) + 1
                continue
            if state not in self.pending:
                self.pending[state] = (raw, now, raw_on)
                if self.debounce > 0:
                    continue
            _, at, at_flags = self.pending[state]
            if now - at < self.debounce:
                continue
            del self.pending[state]
            self.on[state] = raw
            if raw:
                self.since[state] = at
                self.seen[state] = set(at_flags)
                events.append(("start", state, at, sorted(at_flags)))
            else:
                events.append(("end", state, at, sorted(self.seen[state]), self.since[state]))
                self.since[state], self.seen[state] = None, set()
        return events, flicker


def pose_frame(pose) -> str:
    """``vector-odom:<origin_id>``; plain ``vector-odom`` if the SDK's pose has no origin id."""
    origin = getattr(pose, "origin_id", None)
    return f"vector-odom:{origin}" if origin is not None else "vector-odom"


def pose_values(robot) -> dict:
    """The pose as the sidecar's ``body`` values (#407), or {} if the SDK gave none."""
    try:
        pose = robot.pose
        p = pose.position
        return {"pose_x_mm": p.x, "pose_y_mm": p.y, "pose_z_mm": p.z,
                "pose_angle_rad": pose.rotation.angle_z.radians, "pose_frame": pose_frame(pose)}
    except AttributeError:
        return {}


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _reading(robot, component: str):
    return getattr(getattr(robot, component, None), "last_sensor_reading", None)


# group -> (modality, unit); unit None where the SDK doesn't state one
GROUPS = {
    "wheels": ("encoder", "mm/s"),
    "imu": ("imu", None),
    "head": ("angle", "rad"),
    "lift": ("height", "mm"),
    "touch": ("force", None),
    "proximity": ("range", "mm"),
}

# group -> {channel: deadband}; a channel missing here (or a whole group) uses
# band 0, so any change signs. Starting values from #438; Sean can tune them
# against real data.
#
# float("inf") means "recorded, never a reason to send": the channel is still
# in every record that is sent (samples and stats) and in heartbeats, but its
# drift alone never signs one. From Vector's docked data on 18 Nov 2026 (#448):
# touch `raw` (the capacitive analog value) and proximity `signal_quality`
# drift constantly at rest, which signed a touch record nearly every window and
# a proximity record in ~83% of them. `pressed`, the proximity booleans and
# distance moves over 25 mm are what mean something; touches also make episodes.
DEADBANDS = {
    "wheels": {"left": 1.0, "right": 1.0},
    "imu": {"accel_x": 50.0, "accel_y": 50.0, "accel_z": 50.0,
            "gyro_x": 0.02, "gyro_y": 0.02, "gyro_z": 0.02},
    "head": {"value": 0.02},
    "lift": {"value": 1.0},
    "touch": {"raw": float("inf"), "pressed": 0},
    "proximity": {"distance": 25.0, "signal_quality": float("inf"), "unobstructed": 0,
                  "found_object": 0, "lift_in_fov": 0},
}


def sensor_values(robot) -> dict[str, dict]:
    """``{group: {channel: value}}`` from an ``anki_vector.Robot`` (attributes only; no calls).

    A one-channel group uses the channel name ``value``. Groups the SDK doesn't give are absent.
    """
    out: dict[str, dict] = {}
    left, right = getattr(robot, "left_wheel_speed_mmps", None), getattr(robot, "right_wheel_speed_mmps", None)
    wheels = {k: v for k, v in (("left", left), ("right", right)) if _number(v)}
    if wheels:
        out["wheels"] = wheels
    imu = {}
    for vec in ("accel", "gyro"):
        val = getattr(robot, vec, None)
        for axis in ("x", "y", "z"):
            a = getattr(val, axis, None)
            if _number(a):
                imu[f"{vec}_{axis}"] = a
    if imu:
        out["imu"] = imu
    for group, attr in (("head", "head_angle_rad"), ("lift", "lift_height_mm")):
        val = getattr(robot, attr, None)
        if _number(val):
            out[group] = {"value": val}
    touch = _reading(robot, "touch")
    if touch is not None:
        t = {}
        if _number(getattr(touch, "raw_touch_value", None)):
            t["raw"] = touch.raw_touch_value
        if isinstance(getattr(touch, "is_being_touched", None), bool):
            t["pressed"] = touch.is_being_touched
        if t:
            out["touch"] = t
    prox = _reading(robot, "proximity")
    if prox is not None:
        p = {}
        dist = getattr(getattr(prox, "distance", None), "distance_mm", None)
        if _number(dist):
            p["distance"] = dist
        if _number(getattr(prox, "signal_quality", None)):
            p["signal_quality"] = prox.signal_quality
        for name, attr in (("unobstructed", "unobstructed"), ("found_object", "found_object"),
                           ("lift_in_fov", "is_lift_in_fov")):
            if isinstance(getattr(prox, attr, None), bool):
                p[name] = getattr(prox, attr)
        if p:
            out["proximity"] = p
    return out


def battery_values(state) -> dict:
    """Health values from ``get_battery_state()``'s response."""
    v = {}
    for name, attr in (("battery_volts", "battery_volts"), ("battery_level", "battery_level"),
                       ("charging", "is_charging"), ("on_charger_platform", "is_on_charger_platform")):
        val = getattr(state, attr, None)
        if isinstance(val, bool) or _number(val):
            v[name] = val
    return v


def run_command(energy: EnergyReflex, emit: Emit, robot, command: str, control_timeout: float = 10.0) -> None:
    """Run one energy command under behavior control, then tell the reflex how it went (#513).

    The adapter connects with ``behavior_control_level=None`` (watch only), but
    0.8.1 opens the behavior-control stream on every connection
    (``connection.py``, ``Connection._connect``: ``self._open_connections()``), so
    control can be taken later: ``robot.conn.request_control(timeout=…)`` (raises
    ``VectorControlTimeoutException`` if it isn't granted) and given back with
    ``robot.conn.release_control(timeout=…)``. ``robot.behavior.drive_on_charger()``
    (``behavior.py``) finds the charger by its marker and backs onto it;
    ``robot.behavior.say_text()`` speaks. Both need control. Control is always
    released, so Vector's own behaviors take over again straight away.
    """
    ok, detail = False, None
    try:
        robot.conn.request_control(timeout=control_timeout)
        try:
            if command == GO_HOME:
                result = robot.behavior.drive_on_charger()
            elif command == ASK_HELP:
                result = robot.behavior.say_text(HELP_TEXT)
            else:
                raise ValueError(f"unknown energy command {command!r}")
            ok = True
            detail = str(getattr(result, "result", "") or "")[:200] or None
        finally:
            try:
                robot.conn.release_control(timeout=control_timeout)
            except Exception as exc:  # noqa: BLE001 - Vector takes control back on his own anyway
                logging.info("vector_sdk: release control: %s", exc)
    except Exception as exc:  # noqa: BLE001 - no control, no connection: the next poll decides
        detail = f"{type(exc).__name__}: {exc}"[:200]
        logging.warning("vector_sdk: energy %s failed: %s", command, detail)
    energy.done(emit, command, ok, detail)


def status_flags(robot) -> dict[str, bool]:
    st = getattr(robot, "status", None)
    return {f: bool(getattr(st, f)) for f in STATUS_FLAGS if st is not None and hasattr(st, f)}


def touching(robot) -> bool | None:
    val = getattr(_reading(robot, "touch"), "is_being_touched", None)
    return bool(val) if val is not None else None


def _count(anim, *names) -> int | None:
    for name in names:
        try:
            val = getattr(anim, name, None)
        except Exception as exc:  # noqa: BLE001 - the robot didn't answer the list request
            logging.info("vector_sdk: %s unavailable: %s", name, exc)
            continue
        if val is not None:
            try:
                return len(val)
            except TypeError:
                continue
    return None


def robot_specs(robot, sdk_version: str | None = None) -> dict:
    """The spec sheet for ``report_specs``: hardware, SDK and firmware versions, animation counts (#408).

    The server's spec sheet has no animation fields, so the counts go in ``notes``.
    """
    firmware = None
    try:
        firmware = getattr(robot.get_version_state(), "os_version", None) or None
    except Exception as exc:  # noqa: BLE001
        logging.info("vector_sdk: version read failed: %s", exc)
    anim = getattr(robot, "anim", None)
    animations = _count(anim, "anim_list")
    triggers = _count(anim, "anim_trigger_list", "anim_list_triggers")
    software = {"harness": "robot-sidecar"}
    if sdk_version:
        software["harness_version"] = f"wirepod_vector_sdk {sdk_version}"[:64]
    if firmware:
        software["os"] = f"Vector firmware {firmware}"[:64]
    notes = [f"animations: {animations if animations is not None else 'not reported'}",
             f"animation_triggers: {triggers if triggers is not None else 'not reported'}",
             f"sdk: {sdk_version or 'unknown'}", f"firmware: {firmware or 'unknown'}"]
    return {"hardware": {"board": "Anki/DDL Vector"}, "software": software,
            "notes": ("Counts from the SDK at connect (lists not kept): " + "; ".join(notes))[:500]}


class VectorSDK(Adapter):
    name = "vector_sdk"
    # Per-channel thresholds for the body-health self-report (#438 part 1).
    health_deadband = {"battery_volts": 0.05, "battery_level": 0, "charging": 0,
                       "on_charger_platform": 0}

    def __init__(self, serial: str | None = None, body_seconds: float = 1.0, face_gap_seconds: float = 10.0,
                 battery_seconds: float = 30.0, flag_seconds: float = 0.1, debounce_seconds: float = DEBOUNCE_SECONDS,
                 stale_seconds: float = STALE_SECONDS, stale_retry_max: float = STALE_RETRY_MAX_SECONDS,
                 home_after_s: float = HOME_AFTER_SECONDS):
        self.serial = serial or None
        # The sidecar's one command (#513): go home after too long off the dock.
        self.energy = EnergyReflex(home_after_s)
        self._level: int | None = None   # battery level, from the last poll
        self._frame: str | None = None   # odometry frame, from the last pose
        self._command: threading.Thread | None = None
        self.body_seconds = body_seconds
        self.face_gap_seconds = face_gap_seconds
        self.battery_seconds = battery_seconds
        self.flag_seconds = flag_seconds
        self.episodes = EpisodeTracker(debounce_seconds)
        self.episode_n = 0
        self.open: dict[str, str] = {}  # state -> key of its start record
        self.faces_last: dict[int, float] = {}
        self.on_specs: Callable[[dict], object] | None = None
        # On the charger, from the battery poll (#449). A plain bool: the SDK
        # thread writes it and the sidecar's loop reads it, so no lock is needed.
        self._on_charger = False
        self._battery_at: float | None = None  # monotonic time of the last on-charger reading (#486)
        self.watch = StreamWatch(stale_seconds)
        self._stale_at: float | None = None  # wall time the stream was found stale; None while healthy
        self._probe: tuple | None = None  # (fingerprint, time) of the first reading on a post-stale connection
        self._stale_battery: tuple | None = None  # (level, charging, on charger) last seen while stale
        self.wake = False  # battery changed while stale: reconnect now
        self.stale_retry_max = max(stale_retry_max, STALE_RETRY_SECONDS)
        self.retry_seconds = STALE_RETRY_SECONDS
        self.reconnects = 0  # reconnects in the current outage
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, env) -> "VectorSDK":
        return cls(env.get("VECTOR_SERIAL"), float(env.get("VECTOR_BODY_SECONDS", "1")),
                   float(env.get("VECTOR_FACE_GAP_SECONDS", "10")), float(env.get("VECTOR_BATTERY_SECONDS", "30")),
                   float(env.get("VECTOR_FLAG_SECONDS", "0.1")),
                   float(env.get("VECTOR_DEBOUNCE_SECONDS", str(DEBOUNCE_SECONDS))),
                   float(env.get("VECTOR_STALE_SECONDS", str(STALE_SECONDS))),
                   float(env.get("VECTOR_STALE_RETRY_MAX_SECONDS", str(STALE_RETRY_MAX_SECONDS))),
                   60 * float(env.get("VECTOR_HOME_AFTER_MINUTES", str(HOME_AFTER_SECONDS / 60))))

    # -- mapping (pure; called from SDK callbacks) ---------------------------
    def on_wake_word(self, emit: Emit, msg=None) -> None:
        emit(Item("record", "event", "wake word", ["voice", "wake-word"], {"source": "vector-sdk"}))

    def on_user_intent(self, emit: Emit, intent: str, data: str | None) -> None:
        emit(Item("record", "event", f"user intent: {intent}"[:280], ["voice", "user-intent"],
                  {"source": "vector-sdk", "intent": intent, "intent_data": data}))

    def on_face(self, emit: Emit, face_id: int, name: str | None, expression: str | None, now: float) -> None:
        if now - self.faces_last.get(face_id, -1e18) < self.face_gap_seconds:
            return
        self.faces_last[face_id] = now
        emit(Item("record", "perception", "face seen", ["vision", "face"],
                  {"source": "vector-sdk", "face_id": face_id, "name": name or None, "expression": expression}))

    def on_flags(self, emit: Emit, flags: dict[str, bool], touch: bool | None, now: float) -> None:
        """One reading of the status flags (``now``: epoch seconds) -> episode records and flicker."""
        if touch is not None:
            flags = {**flags, "is_touched": touch}
        events, flicker = self.episodes.update(flags, now)
        for ev in events:
            state, at, raw = ev[1], ev[2], ev[3]
            base = {"source": "vector-sdk", "state": state, "flags": raw}
            if ev[0] == "start":
                self.episode_n += 1
                key = self.open[state] = f"ep:{state}:{self.episode_n}"
                emit(Item("record", "event", f"{state} started", ["body", "episode-start", state], base,
                          key=key, at=at))
            else:
                started_at, key = ev[4], self.open.pop(state, None)
                payload = dict(base)
                if started_at is not None:
                    payload["duration_s"] = round(at - started_at, 3)
                emit(Item("record", "event", f"{state} ended", ["body", "episode-end", state], payload,
                          refs={"start": key} if key else None, at=at))
        if flicker:
            emit(Item("flicker", values=flicker))

    def on_body(self, emit: Emit, robot, now: float | None = None) -> None:
        """One body reading: pose (#407) and one sample per sensor group (#408).

        Raises ``StaleStream`` (after signing ``body stream stale``) when the live
        channels have been bit-identical for ``stale_seconds`` (#486).

        After a stale stream, a new connection isn't trusted on sight: nothing is
        sampled until a live channel actually changes, and only then is ``body
        stream resumed`` signed. If they stay bit-identical for another
        ``stale_seconds``, ``StaleStream`` is raised again (no second stale record).
        """
        groups = sensor_values(robot)
        now = time.monotonic() if now is None else now
        if self._stale_at is not None and not self._probe_live(emit, groups, now):
            return  # still proving the stream is live: sign no readings from it
        frozen = self.watch.update(groups, now)
        if frozen is not None:
            self.mark_stale(emit, frozen)
            raise StaleStream(f"no new state for {frozen:.0f} s")
        pose = pose_values(robot)
        if pose:
            self._frame = pose.get("pose_frame")
            emit(Item("body", values=pose))
        for group, values in groups.items():
            modality, unit = GROUPS[group]
            emit(Item("sample", values=values,
                      sensor={"sensor": group, "modality": modality, "unit": unit,
                              "deadband": DEADBANDS.get(group, {})}))

    def _probe_live(self, emit: Emit, groups: dict[str, dict], now: float) -> bool:
        """While stale: True once the live channels change (state is flowing again)."""
        fp = StreamWatch.fingerprint(groups)
        if fp is None or self.watch.stale_seconds <= 0:
            self.mark_resumed(emit)  # nothing to compare: trust the new connection
            return True
        if self._probe is None:
            self._probe = (fp, now)
            return False
        if fp == self._probe[0]:
            if now - self._probe[1] >= self.watch.stale_seconds:
                raise StaleStream(f"still no new state {now - self._probe[1]:.0f} s after reconnecting")
            return False
        self.mark_resumed(emit)
        return True

    def on_battery(self, emit: Emit, state, now: float | None = None) -> None:
        """One battery poll: the health item, and whether Vector is on the charger (#449).

        The battery poll is a direct request, not the state stream, so it stays
        live when the stream freezes. While stale, a change in level, charging or
        charger (Vector woke, was docked or picked up) sets ``wake``: reconnect now.
        """
        health = battery_values(state)
        if self._stale_at is not None:
            seen = tuple(health.get(k) for k in ("battery_level", "charging", "on_charger_platform"))
            if self._stale_battery is not None and seen != self._stale_battery:
                self.wake = True
            self._stale_battery = seen
        if _number(health.get("battery_level")):
            self._level = int(health["battery_level"])
        if isinstance(health.get("on_charger_platform"), bool):
            self._on_charger = health["on_charger_platform"]
            self._battery_at = time.monotonic() if now is None else now
        if health:
            emit(Item("health", values=health))

    def on_energy(self, emit: Emit, robot, now: float | None = None) -> str | None:
        """After a battery poll: feed the energy reflex, and start its command if it gives one (#513).

        Only the battery poll's charger flag counts (a live request, #498). No
        command while Vector is held or falling (the episode tracker's state), or while one is
        still running. Returns the command started, if any.
        """
        if self._battery_at is None:
            return None
        on = self.episodes.on  # the current state, including one already on at connect
        blocked = bool(on.get("held") or on.get("falling"))
        command = self.energy.poll(emit, self._on_charger, self._level, blocked, self._frame,
                                   time.monotonic() if now is None else now)
        if command:
            self._command = threading.Thread(target=run_command, args=(self.energy, emit, robot, command),
                                             name=f"vector-{command}", daemon=True)
            self._command.start()
        return command

    def resting(self, now: float | None = None) -> bool:
        """Docked: on the charger platform, per the last battery reading (False before the first).

        A reading older than three battery polls doesn't count (#486), and nor does
        anything while the stream is stale.
        """
        if not self._on_charger or self._stale_at is not None:
            return False
        if self._battery_at is None:
            return True
        age = (time.monotonic() if now is None else now) - self._battery_at
        return age <= 3 * self.battery_seconds

    def mark_stale(self, emit: Emit, frozen_s: float) -> None:
        """Sign ``body stream stale`` once per outage, and stop trusting old state (#486)."""
        if self._stale_at is not None:
            return
        self._stale_at = time.time()
        self.energy.stream_stale()
        self._stale_battery = None
        self._on_charger = False
        logging.warning("vector_sdk: no new state for %.0f s; reconnecting", frozen_s)
        emit(Item("record", "event", "body stream stale", ["body", "stream-stale"],
                  {"source": "vector-sdk", "unchanged_s": round(frozen_s, 1),
                   "channels": sorted(f"{g}.{c}" for g, cs in LIVE_CHANNELS.items() for c in cs)}))

    def mark_resumed(self, emit: Emit) -> None:
        """After a reconnect: sign ``body stream resumed`` with the outage length (#486)."""
        self.watch.reset()
        self._probe = None
        if self._stale_at is None:
            return
        outage = time.time() - self._stale_at
        reconnects, self.reconnects = self.reconnects, 0
        self._stale_at = self._stale_battery = None
        self.wake = False
        self.retry_seconds = STALE_RETRY_SECONDS
        logging.info("vector_sdk: state flowing again after %.0f s (%d reconnects)", outage, reconnects)
        emit(Item("record", "event", "body stream resumed", ["body", "stream-resumed"],
                  {"source": "vector-sdk", "outage_s": round(outage, 1), "reconnects": reconnects}))

    def next_retry(self) -> float:
        """Seconds to wait before reconnecting after ``StaleStream``: 2 s, doubling to
        ``stale_retry_max`` while the stream stays frozen; 2 s again after a wake."""
        if self.wake:
            self.wake = False
            self.retry_seconds = STALE_RETRY_SECONDS
        wait = self.retry_seconds
        self.retry_seconds = min(self.retry_seconds * 2, self.stale_retry_max)
        self.reconnects += 1
        return wait

    def on_connected(self, robot) -> None:
        """Report the spec sheet once per connection, if the sidecar asked for it."""
        if self.on_specs is None:
            return
        try:
            import anki_vector
            sdk = getattr(anki_vector, "__version__", None)
        except ImportError:
            sdk = None
        specs = robot_specs(robot, sdk)
        logging.info("vector_sdk: %s", specs["notes"])
        self.on_specs(specs)

    # -- the SDK connection ----------------------------------------------------
    def start(self, emit: Emit) -> None:
        self._thread = threading.Thread(target=self._run, args=(emit,), name="vector-sdk", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, emit: Emit) -> None:
        while not self._stop.is_set():
            wait = 30
            try:
                self._connect_and_watch(emit)
            except StaleStream as exc:
                wait = self.next_retry()  # the robot answers; only our stream froze (#486)
                logging.info("vector_sdk: %s; reconnecting in %.0f s", exc, wait)
            except Exception as exc:  # noqa: BLE001 - robot asleep or out of range: retry
                logging.warning("vector_sdk: %s; retrying in 30 s", exc)
            self._stop.wait(wait)

    def _connect_and_watch(self, emit: Emit) -> None:
        import anki_vector  # wirepod_vector_sdk; imported here so tests need no SDK
        from anki_vector.events import Events
        from anki_vector.user_intent import UserIntent

        # behavior_control_level=None: watch only. The energy reflex (#513) takes
        # control briefly, by request, and always gives it back.
        with anki_vector.Robot(self.serial, behavior_control_level=None, enable_face_detection=True) as robot:
            robot.events.subscribe(lambda r, event_type, event: self.on_wake_word(emit), Events.wake_word)

            def intent(r, event_type, event):
                ui = UserIntent(event)
                self.on_user_intent(emit, ui.intent_event.name, ui.intent_data)
            robot.events.subscribe(intent, Events.user_intent)

            def face(r, event_type, event):
                expr = getattr(event, "expression", None)
                self.on_face(emit, int(getattr(event, "face_id", -1)), getattr(event, "name", None),
                             str(expr) if expr is not None else None, time.monotonic())
            robot.events.subscribe(face, Events.robot_observed_face)

            try:
                self.on_connected(robot)
            except Exception as exc:  # noqa: BLE001 - specs are best effort
                logging.info("vector_sdk: specs report failed: %s", exc)

            # A fresh connection: if the last one went stale, on_body proves this
            # one live (a live channel changes) before signing "resumed".
            self.watch.reset()
            self._probe = None
            next_body = next_battery = 0.0
            while not self._stop.is_set():
                now = time.monotonic()
                self.on_flags(emit, status_flags(robot), touching(robot), time.time())
                if now >= next_body:
                    next_body = now + self.body_seconds
                    self.on_body(emit, robot)
                if now >= next_battery:
                    next_battery = now + self.battery_seconds
                    try:
                        self.on_battery(emit, robot.get_battery_state())
                    except Exception as exc:  # noqa: BLE001
                        logging.info("vector_sdk: battery read failed: %s", exc)
                    self.on_energy(emit, robot)
                    if self.wake:
                        raise StaleStream("battery state changed while the stream was stale")
                self._stop.wait(self.flag_seconds)
