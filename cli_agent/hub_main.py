"""Hub entrypoint for the Jetson: MQTT records in, LTMS relay out, acks back.

Run as ``python -m cli_agent.hub_main``. Connects to the local Mosquitto broker,
feeds every ``<swarm>/<member>/records`` message into ``Hub.on_record``, runs
``Hub.tick()`` on a timer, and publishes each member's LTMS-acknowledged head
back on its acks topic. Configuration comes from the environment only.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass
from typing import Mapping

from cli_agent.client import LTMSClient
from cli_agent.hub import Hub
from cli_agent.mqtt_messages import DEFAULT_SWARM
from cli_agent.outbox import Outbox


@dataclass(frozen=True)
class HubConfig:
    ltms_server: str           # LTMS_SERVER          (required)
    key_path: str              # LTMS_KEY_PATH        (required)
    outbox_path: str           # HUB_OUTBOX_PATH      (default "hub-outbox.db")
    mqtt_host: str             # MQTT_HOST            (default "localhost")
    mqtt_port: int             # MQTT_PORT            (default 1883)
    mqtt_username: str | None  # MQTT_USERNAME        (default None)
    mqtt_password: str | None  # MQTT_PASSWORD        (default None)
    swarm: str                 # SWARM                (default "echo-base")
    tick_seconds: float        # TICK_SECONDS         (default 10.0; must be > 0)


def load_config(env: Mapping[str, str]) -> HubConfig:
    try:
        ltms_server = env["LTMS_SERVER"]
    except KeyError:
        raise ValueError("missing LTMS_SERVER") from None
    try:
        key_path = env["LTMS_KEY_PATH"]
    except KeyError:
        raise ValueError("missing LTMS_KEY_PATH") from None

    outbox_path = env.get("HUB_OUTBOX_PATH", "hub-outbox.db")
    mqtt_host = env.get("MQTT_HOST", "localhost")

    try:
        mqtt_port = int(env.get("MQTT_PORT", "1883"))
    except (TypeError, ValueError):
        raise ValueError("MQTT_PORT must be an integer") from None
    if mqtt_port <= 0:
        raise ValueError("MQTT_PORT must be > 0")

    mqtt_username = env.get("MQTT_USERNAME")
    mqtt_password = env.get("MQTT_PASSWORD")  # never logged or put in an error

    swarm = env.get("SWARM", DEFAULT_SWARM)

    try:
        tick_seconds = float(env.get("TICK_SECONDS", "10.0"))
    except (TypeError, ValueError):
        raise ValueError("TICK_SECONDS must be a number") from None
    if tick_seconds <= 0:
        raise ValueError("TICK_SECONDS must be > 0")

    return HubConfig(
        ltms_server=ltms_server,
        key_path=key_path,
        outbox_path=outbox_path,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        mqtt_username=mqtt_username,
        mqtt_password=mqtt_password,
        swarm=swarm,
        tick_seconds=tick_seconds,
    )


def records_subscription(swarm: str) -> str:
    return f"{swarm}/+/records"


def make_publish(mqtt_client):
    def publish(topic: str, payload: bytes, retain: bool = False) -> None:
        mqtt_client.publish(topic, payload, qos=1, retain=retain)

    return publish


def make_on_message(hub):
    def on_message(client, userdata, msg) -> None:
        try:
            hub.on_record(msg.topic, msg.payload)
        except Exception:  # a bad message must never kill the MQTT thread
            logging.exception("Hub: on_message failed for %s", getattr(msg, "topic", None))

    return on_message


def make_on_connect(swarm: str):
    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        try:
            client.subscribe(records_subscription(swarm), qos=1)
            logging.info(
                "Hub: MQTT connected (reason %s), subscribed to %s",
                reason_code,
                records_subscription(swarm),
            )
        except Exception:  # never let a reconnect handler raise
            logging.exception("Hub: on_connect failed")

    return on_connect


def main() -> None:
    # paho is imported here so the rest of the module imports without it.
    import paho.mqtt.client as mqtt

    cfg = load_config(os.environ)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ltms_client = LTMSClient(cfg.ltms_server, key_path=cfg.key_path)
    outbox = Outbox(cfg.outbox_path)

    # clean_session=False: the broker queues QoS 1 records for the hub while it is down.
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="jetson-hub", clean_session=False)
    publish = make_publish(mqtt_client)
    hub = Hub(ltms_client, outbox, publish, cfg.swarm)
    mqtt_client.on_connect = make_on_connect(cfg.swarm)
    mqtt_client.on_message = make_on_message(hub)
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

    try:
        while running:
            hub.tick()
            if hub.stuck:
                for agent, info in hub.stuck.items():
                    logging.warning(
                        "Hub: stuck agent %s: code=%s seq=%s",
                        agent,
                        info.get("code"),
                        info.get("seq"),
                    )
            time.sleep(cfg.tick_seconds)
    finally:
        mqtt_client.loop_stop()
        outbox.close()


if __name__ == "__main__":
    main()
