"""Events and body state from a Vector robot through its Python SDK (#312, #407, #408).

Uses ``wirepod_vector_sdk`` (the ``anki_vector`` package, version 0.8.1 read
for this adapter), connected to a robot served by wire-pod. Event names and
fields come from that package: ``anki_vector/events.py`` (``Events``),
``anki_vector/status.py`` (``RobotStatus``), ``anki_vector/touch.py``,
``anki_vector/proximity.py``, ``anki_vector/animation.py``,
``anki_vector/robot.py`` (``get_battery_state``, ``get_version_state``).
Everything outside the event stream is read with ``getattr`` and skipped when
absent, so an SDK without it records less, never a made-up value.

The adapter only **watches**: it never requests behavior control, so Vector
behaves as Vector. Configure the SDK the usual way first
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

Settings: VECTOR_SERIAL (optional; the SDK's default robot otherwise),
VECTOR_BODY_SECONDS, VECTOR_FLAG_SECONDS, VECTOR_DEBOUNCE_SECONDS,
VECTOR_FACE_GAP_SECONDS, VECTOR_BATTERY_SECONDS.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from adapters.base import Adapter, Emit, Item

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
                 battery_seconds: float = 30.0, flag_seconds: float = 0.1, debounce_seconds: float = DEBOUNCE_SECONDS):
        self.serial = serial or None
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
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, env) -> "VectorSDK":
        return cls(env.get("VECTOR_SERIAL"), float(env.get("VECTOR_BODY_SECONDS", "1")),
                   float(env.get("VECTOR_FACE_GAP_SECONDS", "10")), float(env.get("VECTOR_BATTERY_SECONDS", "30")),
                   float(env.get("VECTOR_FLAG_SECONDS", "0.1")),
                   float(env.get("VECTOR_DEBOUNCE_SECONDS", str(DEBOUNCE_SECONDS))))

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

    def on_body(self, emit: Emit, robot) -> None:
        """One body reading: pose (#407) and one sample per sensor group (#408)."""
        pose = pose_values(robot)
        if pose:
            emit(Item("body", values=pose))
        for group, values in sensor_values(robot).items():
            modality, unit = GROUPS[group]
            emit(Item("sample", values=values,
                      sensor={"sensor": group, "modality": modality, "unit": unit,
                              "deadband": DEADBANDS.get(group, {})}))

    def on_battery(self, emit: Emit, state) -> None:
        """One battery poll: the health item, and whether Vector is on the charger (#449)."""
        health = battery_values(state)
        if isinstance(health.get("on_charger_platform"), bool):
            self._on_charger = health["on_charger_platform"]
        if health:
            emit(Item("health", values=health))

    def resting(self) -> bool:
        """Docked: on the charger platform, per the last battery reading (False before the first)."""
        return self._on_charger

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
            try:
                self._connect_and_watch(emit)
            except Exception as exc:  # noqa: BLE001 - robot asleep or out of range: retry
                logging.warning("vector_sdk: %s; retrying in 30 s", exc)
            self._stop.wait(30)

    def _connect_and_watch(self, emit: Emit) -> None:
        import anki_vector  # wirepod_vector_sdk; imported here so tests need no SDK
        from anki_vector.events import Events
        from anki_vector.user_intent import UserIntent

        # behavior_control_level=None: watch only, never take control from Vector.
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
                self._stop.wait(self.flag_seconds)
