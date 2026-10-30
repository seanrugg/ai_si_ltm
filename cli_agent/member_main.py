"""Member entrypoint: a robot's heartbeat node on MQTT.

Run as ``python -m cli_agent.member_main``. Publishes a signed heartbeat on a
timer through MemberLink, resends anything unacknowledged after (re)connect, and
prunes the outbox on acks. Configuration comes from the environment only.

Threading rule: paho delivers messages on its own thread, but MemberLink's
outbox is main-thread only. ``on_message`` only puts the payload on a queue;
the main loop drains it.
"""
from __future__ import annotations

import logging
import os
import queue
import signal
import threading
import time
from dataclasses import dataclass
from typing import Mapping

from cli_agent.member_link import MemberLink
from cli_agent.mqtt_messages import DEFAULT_SWARM, acks_topic
from cli_agent.outbox import Outbox
from ltms_contract import load_key


@dataclass(frozen=True)
class MemberConfig:
    key_path: str              # LTMS_KEY_PATH        (required; its name is the member name)
    outbox_path: str           # MEMBER_OUTBOX_PATH   (default "member-outbox.db")
    state_path: str            # MEMBER_STATE_PATH    (default "member-head.json")
    mqtt_host: str             # MQTT_HOST            (default "localhost")
    mqtt_port: int             # MQTT_PORT            (default 1883)
    mqtt_username: str | None  # MQTT_USERNAME
    mqtt_password: str | None  # MQTT_PASSWORD
    swarm: str                 # SWARM                (default "echo-base")
    heartbeat_seconds: float   # HEARTBEAT_SECONDS    (default 60.0; must be > 0)


def load_config(env: Mapping[str, str]) -> MemberConfig:
    try:
        key_path = env["LTMS_KEY_PATH"]
    except KeyError:
        raise ValueError("missing LTMS_KEY_PATH")

    outbox_path = env.get("MEMBER_OUTBOX_PATH", "member-outbox.db")
    state_path = env.get("MEMBER_STATE_PATH", "member-head.json")
    mqtt_host = env.get("MQTT_HOST", "localhost")

    try:
        mqtt_port = int(env.get("MQTT_PORT", "1883"))
    except (TypeError, ValueError):
        raise ValueError("MQTT_PORT must be an integer")
    if mqtt_port <= 0:
        raise ValueError("MQTT_PORT must be > 0")

    mqtt_username = env.get("MQTT_USERNAME")
    mqtt_password = env.get("MQTT_PASSWORD")  # never logged or put in an error

    swarm = env.get("SWARM", DEFAULT_SWARM)

    try:
        heartbeat_seconds = float(env.get("HEARTBEAT_SECONDS", "60.0"))
    except (TypeError, ValueError):
        raise ValueError("HEARTBEAT_SECONDS must be a number")
    if heartbeat_seconds <= 0:
        raise ValueError("HEARTBEAT_SECONDS must be > 0")

    return MemberConfig(
        key_path=key_path,
        outbox_path=outbox_path,
        state_path=state_path,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        mqtt_username=mqtt_username,
        mqtt_password=mqtt_password,
        swarm=swarm,
        heartbeat_seconds=heartbeat_seconds,
    )


def make_on_message(acks: "queue.Queue[bytes]"):
    def on_message(client, userdata, msg) -> None:
        try:
            acks.put(msg.payload)
        except Exception:  # never raise on the MQTT thread
            logging.exception("Member: on_message failed")

    return on_message


def make_on_connect(acks_topic_name: str, wanted: threading.Event):
    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        try:
            client.subscribe(acks_topic_name, qos=1)
            wanted.set()
            logging.info(
                "Member: MQTT connected (reason %s), subscribed to %s",
                reason_code,
                acks_topic_name,
            )
        except Exception:  # never raise on the MQTT thread
            logging.exception("Member: on_connect failed")

    return on_connect


def drain_acks(link, acks: "queue.Queue[bytes]") -> int:
    pruned = 0
    while True:
        try:
            payload = acks.get_nowait()
        except queue.Empty:
            break
        try:
            pruned += link.on_ack(payload)
        except ValueError as exc:  # bad payload: log and skip, keep draining
            logging.warning("Member: skipped bad ack: %s", exc)
    return pruned


def heartbeat(link, started_at: float, now: float) -> dict:
    return link.append(
        "perception",
        summary="heartbeat",
        tags=["telemetry", "internal"],
        payload={"uptime_s": round(now - started_at, 1)},
    )


def main() -> None:
    # paho is imported here so the rest of the module imports without it.
    import paho.mqtt.client as mqtt

    cfg = load_config(os.environ)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    name, key = load_key(cfg.key_path)
    outbox = Outbox(cfg.outbox_path)

    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name)
    acks: "queue.Queue[bytes]" = queue.Queue()
    resend_wanted = threading.Event()

    def publish(topic: str, payload: bytes, retain: bool = False) -> None:
        mqtt_client.publish(topic, payload, qos=1, retain=retain)

    link = MemberLink(name, key, outbox, cfg.state_path, publish, cfg.swarm)
    mqtt_client.on_connect = make_on_connect(acks_topic(name, cfg.swarm), resend_wanted)
    mqtt_client.on_message = make_on_message(acks)
    if cfg.mqtt_username is not None:
        mqtt_client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password)
    mqtt_client.connect_async(cfg.mqtt_host, cfg.mqtt_port)
    mqtt_client.loop_start()

    running = True

    def stop(signum, frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    started_at = time.monotonic()
    next_heartbeat = started_at + cfg.heartbeat_seconds
    try:
        while running:
            drain_acks(link, acks)
            if resend_wanted.is_set():
                resend_wanted.clear()
                link.resend_pending()
            now = time.monotonic()
            if now >= next_heartbeat:
                heartbeat(link, started_at, now)
                next_heartbeat = now + cfg.heartbeat_seconds
            # Sleep up to 1 second, waking early if asked to stop.
            deadline = time.monotonic() + 1.0
            while running and time.monotonic() < deadline:
                time.sleep(min(0.25, deadline - time.monotonic()))
    finally:
        mqtt_client.loop_stop()
        outbox.close()


if __name__ == "__main__":
    main()
