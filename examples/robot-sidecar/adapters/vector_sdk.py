"""Events and body state from a Vector robot through its Python SDK (#312).

Uses ``wirepod_vector_sdk`` (the ``anki_vector`` package, version 0.8.1 read
for this adapter), connected to a robot served by wire-pod. Event names and
fields come from that package: ``anki_vector/events.py`` (``Events``),
``anki_vector/status.py`` (``RobotStatus.is_*``), ``anki_vector/touch.py``,
``anki_vector/robot.py`` (``get_battery_state``).

The adapter only **watches**: it never requests behavior control, so Vector
behaves as Vector. Configure the SDK the usual way first
(``python -m anki_vector.configure``); this adapter needs no secrets of its own.

Records (one per event):

| SDK | Record |
|---|---|
| ``wake_word`` | ``event`` "wake word", tags voice,wake-word |
| ``user_intent`` | ``event`` "user intent: <intent>", tags voice,user-intent; the SDK's ``intent_data`` in the payload |
| ``robot_observed_face`` | ``perception`` "face seen", tags vision,face (face id, name if enrolled, expression); at most one per face per ``VECTOR_FACE_GAP_SECONDS`` (default 10) |
| a ``RobotStatus`` flag turning on or off: picked up, being held, cliff detected, on charger, charging, falling, button pressed | ``event`` "<flag> on/off", tags body,state-change |
| touch starting or stopping (``touch.last_sensor_reading.is_being_touched``) | ``event`` "touch on/off", tags body,touch |

Body state (folded by the sidecar into one ``internal,telemetry,body`` record
per window): pose x/y/z (mm) and angle (rad), head angle (rad), lift height
(mm), wheel speeds (mm/s), accelerometer and gyro (x/y/z), raw touch value,
and, every ``VECTOR_BATTERY_SECONDS`` (default 30), battery volts and level.

Not available from the SDK, so not recorded: Vector's stimulation level.

Settings: VECTOR_SERIAL (optional; the SDK's default robot otherwise),
VECTOR_BODY_SECONDS (default 1, how often robot state is sampled),
VECTOR_FACE_GAP_SECONDS, VECTOR_BATTERY_SECONDS.
"""
from __future__ import annotations

import logging
import threading
import time

from adapters.base import Adapter, Emit, Item

STATUS_FLAGS = ("is_picked_up", "is_being_held", "is_cliff_detected", "is_on_charger", "is_charging",
                "is_falling", "is_button_pressed")


def flag_name(flag: str) -> str:
    return flag[3:].replace("_", " ")


class StateTracker:
    """Turns successive readings into on/off events. Pure: easy to test."""

    def __init__(self):
        self.last: dict[str, bool] = {}

    def changes(self, flags: dict[str, bool]) -> list[tuple[str, bool]]:
        out = []
        for k, v in flags.items():
            if k in self.last and self.last[k] != v:
                out.append((k, v))
            self.last[k] = v
        return out


def body_values(robot) -> dict[str, float]:
    """Numeric body state from an ``anki_vector.Robot`` (attributes only; no calls to the robot)."""
    v = {}
    try:
        p = robot.pose.position
        v.update(pose_x_mm=p.x, pose_y_mm=p.y, pose_z_mm=p.z, pose_angle_rad=robot.pose.rotation.angle_z.radians)
    except AttributeError:
        pass
    for name in ("head_angle_rad", "lift_height_mm", "left_wheel_speed_mmps", "right_wheel_speed_mmps"):
        val = getattr(robot, name, None)
        if isinstance(val, (int, float)):
            v[name] = val
    for vec in ("accel", "gyro"):
        val = getattr(robot, vec, None)
        if val is not None:
            v.update({f"{vec}_x": val.x, f"{vec}_y": val.y, f"{vec}_z": val.z})
    try:
        v["touch_raw"] = robot.touch.last_sensor_reading.raw_touch_value
    except AttributeError:
        pass
    return v


def status_flags(robot) -> dict[str, bool]:
    st = getattr(robot, "status", None)
    return {f: bool(getattr(st, f)) for f in STATUS_FLAGS if st is not None and hasattr(st, f)}


def touching(robot) -> bool | None:
    try:
        return bool(robot.touch.last_sensor_reading.is_being_touched)
    except AttributeError:
        return None


class VectorSDK(Adapter):
    name = "vector_sdk"

    def __init__(self, serial: str | None = None, body_seconds: float = 1.0, face_gap_seconds: float = 10.0,
                 battery_seconds: float = 30.0):
        self.serial = serial or None
        self.body_seconds = body_seconds
        self.face_gap_seconds = face_gap_seconds
        self.battery_seconds = battery_seconds
        self.flags = StateTracker()
        self.faces_last: dict[int, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, env) -> "VectorSDK":
        return cls(env.get("VECTOR_SERIAL"), float(env.get("VECTOR_BODY_SECONDS", "1")),
                   float(env.get("VECTOR_FACE_GAP_SECONDS", "10")), float(env.get("VECTOR_BATTERY_SECONDS", "30")))

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

    def on_state(self, emit: Emit, flags: dict[str, bool], touch: bool | None, values: dict[str, float]) -> None:
        if touch is not None:
            flags = {**flags, "is_touched": touch}
        for flag, on in self.flags.changes(flags):
            tags = ["body", "touch"] if flag == "is_touched" else ["body", "state-change"]
            name = "touch" if flag == "is_touched" else flag_name(flag)
            emit(Item("record", "event", f"{name} {'on' if on else 'off'}", tags,
                      {"source": "vector-sdk", "flag": flag, "value": on}))
        if values:
            emit(Item("body", values=values))

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

            next_battery = 0.0
            while not self._stop.is_set():
                values = body_values(robot)
                now = time.monotonic()
                if now >= next_battery:
                    next_battery = now + self.battery_seconds
                    try:
                        b = robot.get_battery_state()
                        values.update(battery_volts=b.battery_volts, battery_level=b.battery_level)
                    except Exception as exc:  # noqa: BLE001
                        logging.info("vector_sdk: battery read failed: %s", exc)
                self.on_state(emit, status_flags(robot), touching(robot), values)
                self._stop.wait(self.body_seconds)
