"""Robot sidecar (#312): a robot that can't run the harness joins LTMS through a
small process on a nearby host. The sidecar holds the **robot's** key and
records what the robot senses and does, and what its voice bridge heard and
answered.

**The sidecar never interprets.** Each thing an adapter reports becomes exactly
one record, with the robot's own values in the payload. The only aggregation is
body state: numeric samples folded into one ``perception`` tagged
``internal,telemetry,body`` per ``BODY_SECONDS`` window (min/max/last), a fixed
statistical window, not a judgement.

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
import os
import queue
import sys
from typing import Iterable

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


class Sidecar:
    """Collects adapter items (from any thread) and signs them on the member's loop."""

    def __init__(self, adapters: Iterable[Adapter], body_seconds: float = 30.0, now: float = 0.0):
        self.adapters = list(adapters)
        self.body_seconds = body_seconds
        self.items: "queue.Queue[Item]" = queue.Queue()
        self.body = BodyWindow()
        self.next_body = now + body_seconds
        self.signed = 0

    def emit(self, item: Item) -> None:
        self.items.put(item)

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
                item = self.items.get_nowait()
            except queue.Empty:
                break
            if item.kind == "body":
                self.body.add(item.values)
                continue
            header = {"summary": item.summary[:280], "tags": item.tags or None}
            if item.peers:
                header["peers"] = item.peers
            link.append(item.type, payload=item.payload, **{k: v for k, v in header.items() if v})
            n += 1
        if now >= self.next_body:
            self.next_body = now + self.body_seconds
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
