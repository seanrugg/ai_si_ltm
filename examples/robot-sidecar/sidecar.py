"""Robot sidecar (#312): a robot that can't run the harness joins LTMS through a
small process on a nearby host. The sidecar holds the **robot's** key and
records what the robot senses and does, and what its voice bridge heard and
answered.

**The sidecar never interprets.** Each thing an adapter reports becomes exactly
one record, with the robot's own values in the payload. The only aggregation is
the body window (``BODY_SECONDS``), a fixed statistical window, not a judgement.
Each window signs (#408):

- **one self-report for health only** (battery, charging; ``health`` items):
  ``perception`` tagged ``internal,telemetry,body-health``, min/max/last per
  value, plus ``stats.flicker``: flag flips too short to be episodes;
- **one sensed record per sensor group** (``sample`` items: wheels, imu, head,
  lift, touch, proximity, …): ``perception`` tagged ``telemetry,body``, never
  ``internal``, in the docs/sensing.md shape (``kind: sensor``, ``sensor``,
  ``modality``, ``unit``, ``samples`` ``[{t, v}]``, ``stats``). A group that
  didn't change is still sent once per window, so a gap means silence, not
  stillness;
- the pose record(s) below.

Records may link to earlier ones: an item's ``refs`` name the ``key`` of an
item already signed, and the sidecar writes ``"<agent>:<seq>"`` (an episode's
end refs its start; an LLM reply refs its request).

**Pose is sensing, not health (#407).** Body samples may carry a pose:
``pose_x_mm``, ``pose_y_mm``, optional ``pose_z_mm``, ``pose_angle_rad`` and
``pose_frame`` (the adapter's name for the odometry frame, e.g.
``vector-odom:<origin id>``). The sidecar takes those out of the body window and,
per window and frame, signs one sensed ``perception`` tagged ``telemetry,pose``
(never ``internal``) in the established pose shape (docs/sensing.md)::

  {"kind": "pose", "frame": "<frame>", "unit": "mm",
   "samples": [{"t": <s from ts>, "x": .., "y": .., "z": .., "theta": <rad>}]}

A sample is kept at most once per ``POSE_SAMPLE_SECONDS`` (1 s), and only when
the pose moved more than ``POSE_MIN_MM`` (5 mm) or ``POSE_MIN_DEG`` (2°) since the
last kept one; the window's last sample is always kept. A window in which nothing
moved signs no pose record. A new frame (Vector's odometry restarts when he's
picked up or delocalized) starts a new track; tracks are never joined.

It runs as an ordinary member (``cli_agent.member_main``): same outbox, chain,
MQTT-to-hub transport and direct fallback, configured by the same environment
variables, plus:

  SIDECAR_ADAPTERS      comma-separated: wirepod, vector_sdk
  BODY_SECONDS          (default 30) body-state window
  SIDECAR_COGNITION_MODELS  "name@where,…": the models the robot's AI uses, for the cognition
                        report (offboard) and the wirepod adapter's replies. Empty: a warning
                        at start, and the profile shows "Language models: not reported".
  adapter settings      see each adapter's module docstring

  python sidecar.py
"""
from __future__ import annotations

import logging
import math
import os
import queue
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from adapters.base import Adapter, Item  # noqa: E402


class BodyWindow:
    """min/max/last per value since the last report (the health self-report)."""

    def __init__(self):
        self.values: dict[str, list[float]] = {}
        self.samples = 0

    def add(self, values: dict) -> None:
        self.samples += 1
        for k, v in values.items():
            if isinstance(v, bool):
                self.values.setdefault(k, []).append(1.0 if v else 0.0)
            elif isinstance(v, (int, float)):
                self.values.setdefault(k, []).append(float(v))

    def report(self, kind: str = "body") -> dict | None:
        if not self.samples:
            return None
        out = {"kind": kind, "samples": self.samples,
               "values": {k: {"min": min(v), "max": max(v), "last": v[-1]} for k, v in sorted(self.values.items())}}
        self.values, self.samples = {}, 0
        return out


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class GroupWindow:
    """One sensor group's readings in a window, sent whole as one sensed record (#408)."""

    def __init__(self, sensor: dict):
        self.sensor = {"sensor": sensor["sensor"], "modality": sensor.get("modality"), "unit": sensor.get("unit")}
        self.samples: list[tuple[float, dict]] = []

    def add(self, at: float, values: dict) -> None:
        self.samples.append((at, dict(values)))

    def report(self) -> tuple[float, dict]:
        t0 = self.samples[0][0]
        channels = sorted({k for _, v in self.samples for k in v})
        single = channels == ["value"]
        samples = [{"t": round(at - t0, 3), "v": v["value"] if single else v} for at, v in self.samples]
        stats: dict = {"count": len(self.samples)}
        for ch in channels:
            vals = [v[ch] for _, v in self.samples if ch in v]
            if vals and all(isinstance(x, bool) for x in vals):
                st = {"true": sum(vals)}
            elif vals and all(_num(x) for x in vals):
                st = {"min": min(vals), "max": max(vals), "mean": round(sum(vals) / len(vals), 6)}
            else:
                continue
            if single:
                stats.update(st)
            else:
                stats[ch] = st
        return t0, {"kind": "sensor", **self.sensor, "samples": samples, "stats": stats}


POSE_KEYS = ("pose_x_mm", "pose_y_mm", "pose_z_mm", "pose_angle_rad", "pose_frame")
POSE_SAMPLE_SECONDS = 1.0
POSE_MIN_MM = 5.0
POSE_MIN_DEG = 2.0


def iso_ts(at: float) -> str:
    """A wall-clock epoch time as the record contract's RFC 3339 ``ts``."""
    return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def split_pose(values: dict) -> tuple[dict | None, dict]:
    """``(pose, rest)``: the pose sample in ``values`` (None if it has no x/y), and the other values."""
    rest = {k: v for k, v in values.items() if k not in POSE_KEYS}
    x, y = values.get("pose_x_mm"), values.get("pose_y_mm")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
        return None, rest
    pose = {"x": x, "y": y}
    z, theta = values.get("pose_z_mm"), values.get("pose_angle_rad")
    if isinstance(z, (int, float)) and not isinstance(z, bool):
        pose["z"] = z
    if isinstance(theta, (int, float)) and not isinstance(theta, bool):
        pose["theta"] = theta
    return pose, rest


def _moved(a: dict, b: dict) -> bool:
    d = math.dist([a["x"], a["y"], a.get("z", 0.0)], [b["x"], b["y"], b.get("z", 0.0)])
    if d > POSE_MIN_MM:
        return True
    if "theta" in a and "theta" in b:
        turn = abs((a["theta"] - b["theta"] + math.pi) % (2 * math.pi) - math.pi)
        return math.degrees(turn) > POSE_MIN_DEG
    return False


class PoseTrack:
    """Pose samples per frame for one window (#407). Thinning only; values are the source's own."""

    def __init__(self):
        self.tracks: list[dict] = []  # {"frame", "kept": [(at, pose)], "pending": (at, pose) | None, "ref"}
        self.last: dict[str, dict] = {}  # frame -> last kept pose from earlier windows (movement reference)

    def add(self, at: float, frame: str, pose: dict) -> None:
        tr = self.tracks[-1] if self.tracks else None
        if tr is None or tr["frame"] != frame:
            # A new frame (or a new window): never joined to the last track.
            tr = {"frame": frame, "kept": [(at, pose)], "pending": None, "ref": self.last.get(frame, pose)}
            self.tracks.append(tr)
            return
        last_at, last_pose = tr["kept"][-1]
        if at - last_at >= POSE_SAMPLE_SECONDS and _moved(pose, last_pose):
            tr["kept"].append((at, pose))
            tr["pending"] = None
        else:
            tr["pending"] = (at, pose)

    def report(self) -> list[tuple[float, dict]]:
        """``[(at, payload)]``: one pose payload per frame that moved in the window; resets the window."""
        out = []
        for tr in self.tracks:
            kept = tr["kept"] + ([tr["pending"]] if tr["pending"] else [])
            self.last[tr["frame"]] = kept[-1][1]
            if not any(_moved(p, tr["ref"]) for _, p in kept):
                continue  # nothing moved: no record
            t0 = kept[0][0]
            out.append((t0, {"kind": "pose", "frame": tr["frame"], "unit": "mm",
                             "samples": [{"t": round(at - t0, 3), **p} for at, p in kept]}))
        # The next window continues in the newest frame only; older frames are closed.
        self.tracks = []
        return out


class Sidecar:
    """Collects adapter items (from any thread) and signs them on the member's loop."""

    def __init__(self, adapters: Iterable[Adapter], body_seconds: float = 30.0, now: float = 0.0,
                 clock: Callable[[], float] = time.time):
        self.adapters = list(adapters)
        self.body_seconds = body_seconds
        self.clock = clock  # wall clock, to time each item when the adapter emits it
        self.items: "queue.Queue[tuple[float, Item]]" = queue.Queue()
        self.health = BodyWindow()
        self.groups: dict[str, GroupWindow] = {}
        self.flicker: dict[str, int] = {}
        self.pose = PoseTrack()
        self.next_body = now + body_seconds
        self.signed = 0
        self.ids: dict[str, str] = {}  # item key -> "<agent>:<seq>" of the record signed for it

    def emit(self, item: Item, at: float | None = None) -> None:
        """Queue an item; ``at`` (epoch seconds) defaults to the item's own, else now."""
        self.items.put((at if at is not None else item.at if item.at is not None else self.clock(), item))

    def start(self) -> None:
        for a in self.adapters:
            a.start(self.emit)
            logging.info("Sidecar: adapter %s started", a.name)

    def stop(self) -> None:
        for a in self.adapters:
            a.stop()

    def _group(self, sensor: dict) -> GroupWindow:
        g = self.groups.get(sensor["sensor"])
        if g is None:
            g = self.groups[sensor["sensor"]] = GroupWindow(sensor)
        return g

    def _sign(self, link, item: Item, at: float) -> None:
        header = {"summary": item.summary[:280], "tags": item.tags or None, "peers": item.peers or None}
        refs = {k: self.ids[v] for k, v in (item.refs or {}).items() if v in self.ids}
        if refs:
            header["refs"] = refs
        if item.at is not None:
            header["ts"] = iso_ts(at)
        rec = link.append(item.type, payload=item.payload, **{k: v for k, v in header.items() if v})
        if item.key and isinstance(rec, dict) and rec.get("agent") and rec.get("seq") is not None:
            self.ids[item.key] = f"{rec['agent']}:{rec['seq']}"
            if len(self.ids) > 2000:  # only recent items refer back
                for k in list(self.ids)[:1000]:
                    del self.ids[k]

    def poll(self, link, now: float) -> int:
        """Sign every queued record, and the body window when it's due. Returns records signed."""
        n = 0
        while True:
            try:
                at, item = self.items.get_nowait()
            except queue.Empty:
                break
            if item.kind == "record":
                self._sign(link, item, at)
                n += 1
            elif item.kind == "body":
                pose, rest = split_pose(item.values)
                if pose is not None:
                    self.pose.add(at, str(item.values.get("pose_frame") or "odom"), pose)
                if rest:
                    self._group({"sensor": "body"}).add(at, rest)
            elif item.kind == "sample":
                self._group(item.sensor).add(at, item.values)
            elif item.kind == "health":
                self.health.add(item.values)
            elif item.kind == "flicker":
                for k, v in item.values.items():
                    self.flicker[k] = self.flicker.get(k, 0) + int(v)
        if now >= self.next_body:
            self.next_body = now + self.body_seconds
            n += self._window(link)
        self.signed += n
        return n

    def _window(self, link) -> int:
        n = 0
        for t0, payload in self.pose.report():
            link.append("perception", summary=f"pose in {payload['frame']}"[:280], tags=["telemetry", "pose"],
                        payload=payload, ts=iso_ts(t0))
            n += 1
        for name in sorted(self.groups):
            t0, payload = self.groups[name].report()
            link.append("perception", summary=f"{name} readings"[:280], tags=["telemetry", "body"],
                        payload=payload, ts=iso_ts(t0))
            n += 1
        self.groups = {}
        health = self.health.report("body-health")
        if health is not None or self.flicker:
            health = health or {"kind": "body-health", "samples": 0, "values": {}}
            if self.flicker:
                health["stats"] = {"flicker": dict(sorted(self.flicker.items()))}
            link.append("perception", summary="body health", tags=["internal", "telemetry", "body-health"],
                        payload=health)
            n += 1
        self.flicker = {}
        return n


def parse_models(spec: str | None) -> list[dict]:
    out = []
    for part in (spec or "").split(","):
        name, _, where = part.strip().partition("@")
        if name.strip():
            out.append({"name": name.strip()[:64], "runs_on": (where.strip() or "bridge")[:64]})
    return out[:4]


MODELS_WARNING = ("Sidecar: SIDECAR_COGNITION_MODELS is empty, so the profile will show 'Language models: "
                  "not reported'; set SIDECAR_COGNITION_MODELS=<model>@<host>")


def cognition_models(env) -> list[dict]:
    """The models from ``SIDECAR_COGNITION_MODELS``; logs a warning when there are none (#408)."""
    models = parse_models(env.get("SIDECAR_COGNITION_MODELS"))
    if not models:
        logging.warning(MODELS_WARNING)
    return models


def build_adapters(env) -> list[Adapter]:
    names = [n.strip() for n in env.get("SIDECAR_ADAPTERS", "").split(",") if n.strip()]
    if not names:
        raise SystemExit("Sidecar: set SIDECAR_ADAPTERS (e.g. wirepod,vector_sdk)")
    out = []
    for n in names:
        if n == "wirepod":
            from adapters.wirepod import WirePodLogs
            out.append(WirePodLogs.from_env(env))
        elif n == "vector_sdk":
            from adapters.vector_sdk import VectorSDK
            out.append(VectorSDK.from_env(env))
        else:
            raise SystemExit(f"Sidecar: unknown adapter {n!r}")
    return out


def main() -> None:
    import time

    from cli_agent import member_main
    from cli_agent.client import LTMSClient

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = os.environ
    models = cognition_models(env)
    adapters = build_adapters(env)
    sidecar = Sidecar(adapters, float(env.get("BODY_SECONDS", "30")), time.monotonic())
    if env.get("LTMS_SERVER") and env.get("LTMS_KEY_PATH"):
        client = LTMSClient(env["LTMS_SERVER"], key_path=env["LTMS_KEY_PATH"])
        # The robot's AI runs off board (in the bridge), so the profile shows it as Hybrid (#185).
        client.report_cognition("offboard", models)
        for a in adapters:
            if hasattr(a, "on_specs"):
                a.on_specs = client.report_specs  # the robot's spec sheet, once connected (#408)
    sidecar.start()
    try:
        member_main.main(poll=sidecar.poll)
    finally:
        sidecar.stop()


if __name__ == "__main__":
    main()
