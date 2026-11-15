"""Robot sidecar (#312): a robot that can't run the harness joins LTMS through a
small process on a nearby host. The sidecar holds the **robot's** key and
records what the robot senses and does, and what its voice bridge heard and
answered.

**The sidecar never interprets.** Each thing an adapter reports becomes exactly
one record, with the robot's own values in the payload. The only aggregation is
body state: numeric samples folded into one ``perception`` tagged
``internal,telemetry,body`` per ``BODY_SECONDS`` window (min/max/last), a fixed
statistical window, not a judgement.

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
  SIDECAR_COGNITION_MODELS  optional "name@where,…" for the cognition report (offboard)
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
    """min/max/last per value since the last report."""

    def __init__(self):
        self.values: dict[str, list[float]] = {}
        self.samples = 0

    def add(self, values: dict[str, float]) -> None:
        self.samples += 1
        for k, v in values.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self.values.setdefault(k, []).append(float(v))
            elif isinstance(v, bool):
                self.values.setdefault(k, []).append(1.0 if v else 0.0)

    def report(self) -> dict | None:
        if not self.samples:
            return None
        out = {"kind": "body", "samples": self.samples,
               "values": {k: {"min": min(v), "max": max(v), "last": v[-1]} for k, v in sorted(self.values.items())}}
        self.values, self.samples = {}, 0
        return out


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
        self.body = BodyWindow()
        self.pose = PoseTrack()
        self.next_body = now + body_seconds
        self.signed = 0

    def emit(self, item: Item, at: float | None = None) -> None:
        """Queue an item; ``at`` (epoch seconds) defaults to now, when the adapter emits it."""
        self.items.put((self.clock() if at is None else at, item))

    def start(self) -> None:
        for a in self.adapters:
            a.start(self.emit)
            logging.info("Sidecar: adapter %s started", a.name)

    def stop(self) -> None:
        for a in self.adapters:
            a.stop()

    def poll(self, link, now: float) -> int:
        """Sign every queued record, and the body window when it's due. Returns records signed."""
        n = 0
        while True:
            try:
                at, item = self.items.get_nowait()
            except queue.Empty:
                break
            if item.kind == "body":
                pose, rest = split_pose(item.values)
                if pose is not None:
                    self.pose.add(at, str(item.values.get("pose_frame") or "odom"), pose)
                if rest:
                    self.body.add(rest)
                continue
            header = {"summary": item.summary[:280], "tags": item.tags or None}
            if item.peers:
                header["peers"] = item.peers
            link.append(item.type, payload=item.payload, **{k: v for k, v in header.items() if v})
            n += 1
        if now >= self.next_body:
            self.next_body = now + self.body_seconds
            for t0, payload in self.pose.report():
                link.append("perception", summary=f"pose in {payload['frame']}"[:280], tags=["telemetry", "pose"],
                            payload=payload, ts=iso_ts(t0))
                n += 1
            report = self.body.report()
            if report is not None:
                link.append("perception", summary="body state", tags=["internal", "telemetry", "body"],
                            payload=report)
                n += 1
        self.signed += n
        return n


def parse_models(spec: str | None) -> list[dict]:
    out = []
    for part in (spec or "").split(","):
        name, _, where = part.strip().partition("@")
        if name.strip():
            out.append({"name": name.strip()[:64], "runs_on": (where.strip() or "bridge")[:64]})
    return out[:4]


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
    sidecar = Sidecar(build_adapters(env), float(env.get("BODY_SECONDS", "30")), time.monotonic())
    if env.get("LTMS_SERVER") and env.get("LTMS_KEY_PATH"):
        # The robot's AI runs off board (in the bridge), so the profile shows it as Hybrid (#185).
        LTMSClient(env["LTMS_SERVER"], key_path=env["LTMS_KEY_PATH"]).report_cognition(
            "offboard", parse_models(env.get("SIDECAR_COGNITION_MODELS")))
    sidecar.start()
    try:
        member_main.main(poll=sidecar.poll)
    finally:
        sidecar.stop()


if __name__ == "__main__":
    main()
