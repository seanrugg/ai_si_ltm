"""Arduino App Lab app: this board as an LTMS swarm member (#299).

App Lab's ``App.run(user_loop=...)`` owns the main thread and SIGTERM, so the
member is ``cli_agent.member_main.MemberNode`` (#491), stepped from that loop.
Everything else is in ``app_member.py`` (testable without App Lab).

On the MCU side, the sketch sends ``mcu_status`` every second and expects a
``link`` call at least every 5 s; without one it shows "disconnected" by itself.
"""
from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if (HERE / "vendor").is_dir():  # make-app.sh puts ltms-harness here
    sys.path.insert(0, str(HERE / "vendor"))
sys.path.insert(0, str(HERE))

from arduino.app_utils import App, Bridge  # noqa: E402

import app_member as am  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("app-lab-member")

DATA = Path(os.environ.get("LTMS_APP_DATA", HERE.parent / "data"))
if am.ensure_data_dir(DATA):
    log.info("First run: wrote %s. Fill it in and restart the app.", DATA / "member.env")

telemetry = am.TelemetryWindow(60.0)
Bridge.provide("mcu_status", telemetry.add)  # the sketch notifies this every second

state = {"phase": "needs-settings", "node": None, "approval": None, "link_at": 0.0, "mcu_ok": True}

env = {**am.read_env_file(DATA / "member.env"), **{k: v for k, v in os.environ.items() if k.isupper()}}
todo = am.problems(env)
if todo:
    log.warning("Not configured yet: %s. Edit %s and restart the app.", "; ".join(todo), DATA / "member.env")
else:
    from cli_agent.client import LTMSClient

    S = am.load(DATA)
    PUBLIC_KEY = am.ensure_key(S)
    log.info("Member %s, public key %s (the approval card shows the first 16: %s)",
             S.name, PUBLIC_KEY, PUBLIC_KEY[:16])
    approval = am.Approval(S, LTMSClient(S.server, key_path=str(S.key_path)), PUBLIC_KEY, am.hardware_note(PUBLIC_KEY))
    state["approval"] = approval
    state["phase"] = approval.phase
    threading.Thread(target=approval.run, name="approval", daemon=True).start()


def start_member() -> None:
    from cli_agent.member_main import MemberNode, load_config

    try:
        node = MemberNode(load_config(S.member_env))
        node.start()
    except Exception as exc:  # noqa: BLE001 - bad settings: say so, keep the MCU fed
        log.error("Member start failed: %s", exc)
        state["phase"] = "member-failed"
        return
    state["node"], state["phase"] = node, "member"
    log.info("Member running as %s", S.name)


def send_link() -> None:
    node = state["node"]
    st = am.link_state(state["phase"], node.status() if node else None)
    try:
        Bridge.call("link", st, timeout=2)
        if not state["mcu_ok"]:
            log.info("MCU answering again")
        state["mcu_ok"] = True
    except Exception as exc:  # noqa: BLE001 - the sketch may be uploading or reset
        if state["mcu_ok"]:
            log.warning("MCU link call failed: %s", exc)
        state["mcu_ok"] = False


def step() -> None:
    approval = state["approval"]
    if approval is not None and state["node"] is None and state["phase"] != "member-failed":
        state["phase"] = approval.phase
        if approval.ready.is_set():
            start_member()
    node = state["node"]
    if node is not None:
        node.step()
        if telemetry.due():
            node.link.append("perception", summary="MCU status (1 min)", tags=["internal", "telemetry"],
                             payload=telemetry.take())
    now = time.monotonic()
    if now - state["link_at"] >= 1.0:
        state["link_at"] = now
        send_link()
    time.sleep(0.2)


def shutdown() -> None:
    if state["approval"] is not None:
        state["approval"].stopping.set()
    if state["node"] is not None:
        state["node"].stop()
    log.info("stopped")


atexit.register(shutdown)
App.run(user_loop=step)
