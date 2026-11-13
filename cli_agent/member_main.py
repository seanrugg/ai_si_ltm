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

from cli_agent.direct_link import DirectLink, Transport
from cli_agent.member_link import MemberLink
from cli_agent.client import local_addresses
from cli_agent.mqtt_messages import (
    acks_topic, agent_from_say_topic, decode_record, encode_hello, hello_topic, is_swarm_message, say_subscription,
    topic_kind,
)
from cli_agent.outbox import Outbox
from cli_agent.swarm_topic import NoSwarmTopic, check_prefix, resolve
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
    swarm: str | None          # SWARM                (deprecated: the swarm's name as topic prefix)
    heartbeat_seconds: float   # HEARTBEAT_SECONDS    (default 60.0; must be > 0)
    swarm_topic_id: str | None = None  # SWARM_TOPIC_ID (preferred, #293)
    ltms_server: str | None = None     # LTMS_SERVER    (optional: topic lookup, and direct mode)
    transport: str = "auto"            # TRANSPORT      mqtt | direct | auto (#297)
    fallback_after_seconds: float = 120.0  # FALLBACK_AFTER_SECONDS (auto: broker down this long -> direct)


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

    swarm = env.get("SWARM") or None
    swarm_topic_id = env.get("SWARM_TOPIC_ID") or None
    ltms_server = env.get("LTMS_SERVER") or None
    transport = env.get("TRANSPORT", "auto")
    if transport not in ("mqtt", "direct", "auto"):
        raise ValueError("TRANSPORT must be mqtt, direct or auto")
    if transport == "direct" and not ltms_server:
        raise ValueError("TRANSPORT=direct needs LTMS_SERVER")
    try:
        fallback_after = float(env.get("FALLBACK_AFTER_SECONDS", "120"))
    except (TypeError, ValueError):
        raise ValueError("FALLBACK_AFTER_SECONDS must be a number") from None
    if fallback_after <= 0:
        raise ValueError("FALLBACK_AFTER_SECONDS must be > 0")
    if transport != "direct" and not (swarm or swarm_topic_id or ltms_server):
        raise ValueError("missing SWARM_TOPIC_ID (or LTMS_SERVER to look it up, or the deprecated SWARM)")
    for value, setting in ((swarm, "SWARM"), (swarm_topic_id, "SWARM_TOPIC_ID")):
        if value is not None:
            check_prefix(value, setting)

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
        swarm_topic_id=swarm_topic_id,
        ltms_server=ltms_server,
        transport=transport,
        fallback_after_seconds=fallback_after,
    )


def member_prefix(cfg: MemberConfig, lookup=None) -> tuple[str, str]:
    """The member's topic prefix and where it came from (#293).

    ``SWARM_TOPIC_ID`` first. A member that still has an explicit ``SWARM=<name>``
    keeps using the name (with a warning naming its topic ID, when LTMS can say)
    rather than switching on its own: the broker's ACL may not allow the new
    topics yet, and the hub serves both during migration. Otherwise the topic ID
    is looked up from LTMS.
    """
    if cfg.swarm_topic_id:
        return resolve(cfg.swarm_topic_id, None)
    if cfg.swarm:
        info = lookup() if lookup is not None else None
        if info and info.get("topic_id") and info["topic_id"] != cfg.swarm:
            logging.warning("Member: this swarm's topic ID is %s; set SWARM_TOPIC_ID=%s once the broker "
                            "allows it", info["topic_id"], info["topic_id"])
        return resolve(None, cfg.swarm)
    return resolve(None, None, lookup)


def make_on_message(acks: "queue.Queue[bytes]", messages: "queue.Queue | None" = None):
    """Acks go to ``acks``; swarm messages (#300) go to ``messages`` as (topic, payload)."""
    def on_message(client, userdata, msg) -> None:
        try:
            if messages is not None and topic_kind(msg.topic) == "say":
                messages.put((msg.topic, msg.payload))
            else:
                acks.put(msg.payload)
        except Exception:  # never raise on the MQTT thread
            logging.exception("Member: on_message failed")

    return on_message


def log_message(rec: dict) -> None:
    """Default handler for swarm messages: log them. Behaviour code replaces this."""
    to = ", ".join(rec.get("peers") or []) or "the swarm"
    logging.info("Member: message from %s to %s: %s", rec["agent"], to, rec.get("summary"))


def drain_messages(messages: "queue.Queue", name: str, prefix: str, handler=log_message) -> int:
    """Decode and hand on every swarm message waiting (main thread only). Never raises."""
    handled = 0
    while True:
        try:
            topic, payload = messages.get_nowait()
        except queue.Empty:
            return handled
        try:
            agent = agent_from_say_topic(topic, prefix)
            if agent == name:
                continue  # our own message, echoed by the broker
            rec = decode_record(payload, agent)
            if not is_swarm_message(rec):
                raise ValueError("not a swarm message")
            handler(rec)
            handled += 1
        except Exception as exc:  # noqa: BLE001 - a bad message or handler must not stop the member
            logging.warning("Member: ignored message on %s: %s", topic, exc)


def make_on_connect(acks_topic_name: str, wanted: threading.Event, hello=None, say_sub: str | None = None):
    """``hello`` is an optional ``() -> (topic, payload)``: published on every connect (#216).
    ``say_sub``: the swarm's messages to subscribe to (#300)."""
    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        try:
            client.subscribe(acks_topic_name, qos=1)
            if say_sub:
                client.subscribe(say_sub, qos=1)
            wanted.set()
            if hello is not None:
                topic, payload = hello()
                client.publish(topic, payload, qos=1, retain=False)
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


def main(poll=None) -> None:
    """Run the member. ``poll(link, now)``, if given, is called once a loop (about every
    second) so a program built on the member (a robot sidecar, #312) can sign its own
    records into the same chain and transport. An exception in it is logged, never fatal.
    """
    # paho is imported here so the rest of the module imports without it.
    import paho.mqtt.client as mqtt

    cfg = load_config(os.environ)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    name, key = load_key(cfg.key_path)
    ltms = None
    if cfg.ltms_server:
        from cli_agent.client import LTMSClient
        ltms = LTMSClient(cfg.ltms_server, key_path=cfg.key_path)
    if cfg.transport == "direct":
        prefix, source = "", "direct"
    else:
        try:
            prefix, source = member_prefix(cfg, ltms.swarm if ltms is not None and not cfg.swarm_topic_id else None)
        except NoSwarmTopic as exc:
            raise SystemExit(f"Member: {exc}") from None
    logging.info("Member: %s, transport %s, topic prefix %s (from %s)", name, cfg.transport, prefix or "-", source)
    outbox = Outbox(cfg.outbox_path)
    if cfg.transport == "auto" and ltms is None:
        logging.info("Member: no LTMS_SERVER, so no direct fallback; MQTT only")
    transport = Transport(cfg.transport, cfg.fallback_after_seconds, time.monotonic(), can_direct=ltms is not None)
    direct = DirectLink(ltms, outbox, name) if ltms is not None else None

    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name)
    acks: "queue.Queue[bytes]" = queue.Queue()
    resend_wanted = threading.Event()

    def publish(topic: str, payload: bytes, retain: bool = False) -> None:
        if cfg.transport == "direct":
            return  # records stay in the outbox; DirectLink sends them
        mqtt_client.publish(topic, payload, qos=1, retain=retain)

    link = MemberLink(name, key, outbox, cfg.state_path, publish, prefix)
    def hello():
        try:
            network = local_addresses()
        except OSError:  # no resolvable hostname: announce without addresses
            network = []
        return hello_topic(name, prefix), encode_hello(name, network)

    messages: "queue.Queue" = queue.Queue()
    connected_cb = make_on_connect(acks_topic(name, prefix), resend_wanted, hello, say_subscription(prefix))

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        transport.on_connect()
        connected_cb(client, userdata, flags, reason_code, properties)

    def on_disconnect(client, userdata, *args) -> None:
        transport.on_disconnect(time.monotonic())

    use_mqtt = cfg.transport != "direct"
    if use_mqtt:
        mqtt_client.on_connect = on_connect
        mqtt_client.on_disconnect = on_disconnect
        mqtt_client.on_message = make_on_message(acks, messages)
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
            drain_messages(messages, name, prefix)
            if resend_wanted.is_set():
                resend_wanted.clear()
                link.resend_pending()
            now = time.monotonic()
            if now >= next_heartbeat:
                # Also resend anything unacked: covers a hub restart while we stayed
                # connected. Duplicates are harmless (hub and LTMS both dedupe).
                link.resend_pending()
                heartbeat(link, started_at, now)
                next_heartbeat = now + cfg.heartbeat_seconds
            if poll is not None:
                try:
                    poll(link, now)
                except Exception:  # noqa: BLE001 - a source's bug must not stop the member
                    logging.exception("Member: poll failed")
            if direct is not None and transport.choose(time.monotonic()) == "direct":
                direct.flush()
            # Sleep up to 1 second, waking early if asked to stop.
            deadline = time.monotonic() + 1.0
            while running and time.monotonic() < deadline:
                time.sleep(min(0.25, deadline - time.monotonic()))
    finally:
        if use_mqtt:
            mqtt_client.loop_stop()
        outbox.close()


if __name__ == "__main__":
    main()
