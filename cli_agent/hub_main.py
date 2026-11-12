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
from cli_agent.hub import HeartbeatTimer, Hub
from cli_agent.member_link import MemberLink
from cli_agent.swarm_topic import check_prefix, resolve
from cli_agent.mqtt_messages import say_subscription, topic_kind
from cli_agent.outbox import Outbox
from ltms_contract import load_key


@dataclass(frozen=True)
class HubConfig:
    ltms_server: str           # LTMS_SERVER          (required)
    key_path: str              # LTMS_KEY_PATH        (required)
    outbox_path: str           # HUB_OUTBOX_PATH      (default "hub-outbox.db")
    mqtt_host: str             # MQTT_HOST            (default "localhost")
    mqtt_port: int             # MQTT_PORT            (default 1883)
    mqtt_username: str | None  # MQTT_USERNAME        (default None)
    mqtt_password: str | None  # MQTT_PASSWORD        (default None)
    swarm: str | None          # SWARM                (deprecated: the swarm's name as topic prefix)
    tick_seconds: float        # TICK_SECONDS         (default 10.0; must be > 0)
    max_msgs_per_second: int = 10      # HUB_MAX_MSGS_PER_SECOND (swarm messages per member, #300)
    heartbeat_seconds: float = 300.0  # HUB_HEARTBEAT_SECONDS (default 300; must be > 0)
    state_path: str = "hub-head.json"  # HUB_STATE_PATH (default: hub-head.json beside the outbox)
    swarm_topic_id: str | None = None  # SWARM_TOPIC_ID (preferred; else looked up from LTMS, #293)
    legacy_name: str | None = None     # SWARM_LEGACY_NAME (migration: also serve <name>/+/...)


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

    # The hub always has LTMS_SERVER, so with neither setting it looks its topic ID up at start (#293).
    swarm = env.get("SWARM") or None
    swarm_topic_id = env.get("SWARM_TOPIC_ID") or None
    legacy_name = env.get("SWARM_LEGACY_NAME") or None
    for value, setting in ((swarm, "SWARM"), (swarm_topic_id, "SWARM_TOPIC_ID"), (legacy_name, "SWARM_LEGACY_NAME")):
        if value is not None:
            check_prefix(value, setting)

    try:
        tick_seconds = float(env.get("TICK_SECONDS", "10.0"))
    except (TypeError, ValueError):
        raise ValueError("TICK_SECONDS must be a number") from None
    if tick_seconds <= 0:
        raise ValueError("TICK_SECONDS must be > 0")

    try:
        max_msgs = int(env.get("HUB_MAX_MSGS_PER_SECOND", "10"))
    except (TypeError, ValueError):
        raise ValueError("HUB_MAX_MSGS_PER_SECOND must be a whole number") from None
    if max_msgs <= 0:
        raise ValueError("HUB_MAX_MSGS_PER_SECOND must be > 0")

    try:
        heartbeat_seconds = float(env.get("HUB_HEARTBEAT_SECONDS", "300"))
    except (TypeError, ValueError):
        raise ValueError("HUB_HEARTBEAT_SECONDS must be a number") from None
    if heartbeat_seconds <= 0:
        raise ValueError("HUB_HEARTBEAT_SECONDS must be > 0")

    state_path = env.get("HUB_STATE_PATH") or os.path.join(os.path.dirname(outbox_path), "hub-head.json")

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
        heartbeat_seconds=heartbeat_seconds,
        state_path=state_path,
        max_msgs_per_second=max_msgs,
        swarm_topic_id=swarm_topic_id,
        legacy_name=legacy_name,
    )


def legacy_prefix(cfg: HubConfig, source: str) -> str | None:
    """The old name-based prefix to keep serving during migration, if any (#293).

    ``SWARM_LEGACY_NAME`` wins. A hub that still has ``SWARM=<name>`` but now
    uses a topic ID also keeps serving the name, so members can move one at a time.
    """
    if cfg.legacy_name:
        return cfg.legacy_name
    return cfg.swarm if source != "name" else None


def records_subscription(swarm: str) -> str:
    return f"{swarm}/+/records"


def hello_subscription(swarm: str) -> str:
    return f"{swarm}/+/hello"


def make_publish(mqtt_client):
    def publish(topic: str, payload: bytes, retain: bool = False) -> None:
        mqtt_client.publish(topic, payload, qos=1, retain=retain)

    return publish


def make_on_message(hub):
    def on_message(client, userdata, msg) -> None:
        try:
            kind = topic_kind(msg.topic)
            if kind == "hello":
                hub.on_hello(msg.topic, msg.payload)
            elif kind == "say":
                hub.on_say(msg.topic, msg.payload)
            else:
                hub.on_record(msg.topic, msg.payload)
        except Exception:  # a bad message must never kill the MQTT thread
            logging.exception("Hub: on_message failed for %s", getattr(msg, "topic", None))

    return on_message


def make_on_connect(swarm):
    """``swarm`` is one topic prefix or a list of them (topic ID first, then a legacy name)."""
    prefixes = [swarm] if isinstance(swarm, str) else list(swarm)

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        try:
            topics = []
            for prefix in prefixes:
                for topic in (records_subscription(prefix), hello_subscription(prefix), say_subscription(prefix)):
                    client.subscribe(topic, qos=1)
                    topics.append(topic)
            logging.info("Hub: MQTT connected (reason %s), subscribed to %s", reason_code, " and ".join(topics))
        except Exception:  # never let a reconnect handler raise
            logging.exception("Hub: on_connect failed")

    return on_connect


def main() -> None:
    # paho is imported here so the rest of the module imports without it.
    import paho.mqtt.client as mqtt

    cfg = load_config(os.environ)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    name, key = load_key(cfg.key_path)
    ltms_client = LTMSClient(cfg.ltms_server, key_path=cfg.key_path)
    outbox = Outbox(cfg.outbox_path)

    # clean_session=False: the broker queues QoS 1 records for the hub while it is down.
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name, clean_session=False)
    publish = make_publish(mqtt_client)
    # Which swarm, in human terms and as a topic prefix (#293).
    info = ltms_client.swarm()
    prefix, source = resolve(cfg.swarm_topic_id, cfg.swarm, lambda: info)
    logging.info("Hub: swarm %s, topic prefix %s (from %s)",
                 (info or {}).get("name", "unknown"), prefix, source)
    hub = Hub(ltms_client, outbox, publish, prefix, name=name, legacy=legacy_prefix(cfg, source),
              max_msgs_per_second=cfg.max_msgs_per_second)
    # The hub's own chain (#281): signed into the shared outbox, never published on MQTT.
    own = MemberLink(name, key, outbox, cfg.state_path, lambda *a, **k: None, prefix)
    started_at = time.monotonic()
    beat = HeartbeatTimer(cfg.heartbeat_seconds, started_at)
    mqtt_client.on_connect = make_on_connect(hub.prefixes)
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
            now = time.monotonic()
            if beat.due(now):
                hub.heartbeat(own, started_at, now)
                current = ltms_client.swarm()  # never raises; keeps the log in human terms
                logging.info("Hub: heartbeat; swarm %s (topic %s)", (current or {}).get("name", "unknown"), prefix)
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
