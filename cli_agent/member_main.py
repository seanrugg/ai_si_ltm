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
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Mapping

from cli_agent.direct_link import DirectLink, Transport
from cli_agent.member_link import MemberLink
from cli_agent.client import local_addresses
from cli_agent.mqtt_messages import (
    acks_topic, agent_from_say_topic, decode_record, encode_hello, hello_topic, is_swarm_message, ping_topic,
    fleet_subscription, say_subscription, topic_kind,
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
    heartbeat_seconds: float   # HEARTBEAT_SECONDS    (default 43200, twice daily; must be > 0, #356)
    swarm_topic_id: str | None = None  # SWARM_TOPIC_ID (preferred, #293)
    ltms_server: str | None = None     # LTMS_SERVER    (optional: topic lookup, and direct mode)
    transport: str = "auto"            # TRANSPORT      mqtt | direct | auto (#297)
    fallback_after_seconds: float = 120.0  # FALLBACK_AFTER_SECONDS (auto: broker down this long -> direct)
    resend_seconds: float = 60.0       # RESEND_SECONDS: resend unacked records this often (#356)
    ping_poll_seconds: float = 30.0    # PING_POLL_SECONDS: with LTMS_SERVER, check for admin pings (#356)
    intro_reply: str = "reply"         # INTRO_REPLY: reply | off, to other members' introductions (#334)
    intro_check_seconds: float = 3600.0  # INTRO_CHECK_SECONDS: how often to look for a new swarm or role
    handoff_path: str | None = None    # MEMBER_HANDOFF_PATH: harnesses on this device record through it (#521)


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
        heartbeat_seconds = float(env.get("HEARTBEAT_SECONDS", "43200"))
    except (TypeError, ValueError):
        raise ValueError("HEARTBEAT_SECONDS must be a number")
    if heartbeat_seconds <= 0:
        raise ValueError("HEARTBEAT_SECONDS must be > 0")
    timers = {}
    for setting, default in (("RESEND_SECONDS", "60"), ("PING_POLL_SECONDS", "30"), ("INTRO_CHECK_SECONDS", "3600")):
        try:
            timers[setting] = float(env.get(setting, default))
        except (TypeError, ValueError):
            raise ValueError(f"{setting} must be a number") from None
        if timers[setting] <= 0:
            raise ValueError(f"{setting} must be > 0")

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
        intro_reply=_intro_reply(env),
        intro_check_seconds=timers["INTRO_CHECK_SECONDS"],
        resend_seconds=timers["RESEND_SECONDS"],
        ping_poll_seconds=timers["PING_POLL_SECONDS"],
        handoff_path=(env.get("MEMBER_HANDOFF_PATH") or "").strip() or None,
    )


def _intro_reply(env) -> str:
    value = (env.get("INTRO_REPLY") or "reply").strip().lower()
    if value not in ("reply", "off"):
        raise ValueError("INTRO_REPLY must be reply or off")
    return value


def make_intro_sender(link, direct: bool):
    """Introductions go on the swarm bus, or (direct transport, #297) straight into the chain."""
    from cli_agent.introductions import INTRO_TAG

    def send(text: str, payload: dict, reply_to: str | None):
        if direct:
            header = {"summary": text, "tags": [INTRO_TAG]}
            if reply_to:
                header["refs"] = {"reply_to": reply_to}
            return link.append("communication", payload=payload, **header)
        return link.send_message(text, payload=payload, reply_to=reply_to, tags=[INTRO_TAG])
    return send


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


def make_on_message(acks: "queue.Queue[bytes]", messages: "queue.Queue | None" = None,
                    pinged: "threading.Event | None" = None, fleet: "queue.Queue | None" = None):
    """Acks go to ``acks``; swarm messages (#300) go to ``messages`` as (topic, payload);
    an admin ping (#356) sets ``pinged``."""
    def on_message(client, userdata, msg) -> None:
        try:
            if pinged is not None and topic_kind(msg.topic) == "ping":
                pinged.set()
            elif fleet is not None and "/fleet" in msg.topic and msg.topic.split("/")[1] == "fleet":
                fleet.put(msg.payload)  # a directive or a cancel (#306)
            elif messages is not None and topic_kind(msg.topic) == "say":
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


def drain_directives(fleet_msgs: "queue.Queue", directives) -> int:
    """Hand every fleet-topic message waiting to the directive handler (#306, #490).

    Main thread only: the handler signs its acks through the member's link. Each
    outcome is logged; a message that breaks the handler is logged and skipped, so
    a bad directive never stops the member. Returns how many were handled.
    """
    handled = 0
    while True:
        try:
            payload = fleet_msgs.get_nowait()
        except queue.Empty:
            return handled
        try:
            result = directives.on_message(payload)
            logging.info("Member: directive message: %s", result)
        except Exception:  # noqa: BLE001 - a bad directive must not stop the member
            logging.exception("Member: directive handling failed")
        handled += 1


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
            # A message from outside the swarm, downlinked by the hub (#305), is addressed
            # to the swarm by its topic ID instead of carrying the swarm-message tag.
            if not is_swarm_message(rec) and f"swarm:{prefix}" not in (rec.get("peers") or []):
                raise ValueError("not a swarm message")
            handler(rec)
            handled += 1
        except Exception as exc:  # noqa: BLE001 - a bad message or handler must not stop the member
            logging.warning("Member: ignored message on %s: %s", topic, exc)


def make_on_connect(acks_topic_name: str, wanted: threading.Event, hello=None, say_sub: str | None = None,
                    ping_sub: str | None = None, fleet_subs: list[str] | None = None):
    """``hello`` is an optional ``() -> (topic, payload)``: published on every connect (#216).
    ``say_sub``: the swarm's messages to subscribe to (#300). ``ping_sub``: admin pings (#356)."""
    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        try:
            client.subscribe(acks_topic_name, qos=1)
            if ping_sub:
                client.subscribe(ping_sub, qos=1)
            for sub in fleet_subs or []:
                client.subscribe(sub, qos=1)
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


def heartbeat(link, started_at: float, now: float, ping: bool = False) -> dict:
    """A self-report that this member is alive. ``ping``: answering an admin's ping (#356)."""
    payload = {"uptime_s": round(now - started_at, 1)}
    if ping:
        payload["ping"] = True
    return link.append(
        "perception",
        summary="heartbeat (ping)" if ping else "heartbeat",
        tags=["telemetry", "internal"],
        payload=payload,
    )


class MemberNode:
    """The member as a node another program drives: ``start()``, ``step()`` on every
    pass of its own loop, ``stop()`` (#491).

    For hosts that own their main thread: Arduino App Lab (``App.run``), a ROS 2
    node's executor, a simulator's step callback, an agent framework. ``main()``
    is this node in a plain loop, so ``ltms-member`` behaves as before.

    - ``on_message(rec)``: each decoded swarm message, after the introducer.
    - ``decide(stmt) -> Decision``: whether to follow a verified directive (default:
      follow). Passed to ``DirectiveHandler``.
    - ``poll(link, now)``: called once a step, so a program can sign its own records
      into the same chain and transport (the robot sidecar, #312).
    - ``submit(fn)``: from another thread, run ``fn(link)`` inside the next
      ``step()``; the result (or exception) comes back through the returned Future.

    ``MemberLink`` and the SQLite outbox are main-thread only: call ``start``,
    ``step`` and ``stop`` from one thread. Hooks run on that thread; an exception in
    one is logged, never fatal.
    """

    def __init__(self, cfg: MemberConfig, on_message=None, decide=None, *, poll=None,
                 env: Mapping[str, str] | None = None, ltms=None, mqtt_factory=None, clock=time.monotonic) -> None:
        self.cfg = cfg
        self.on_message_hook, self.decide, self.poll = on_message, decide, poll
        self.env = os.environ if env is None else env
        self.ltms, self._ltms_given = ltms, ltms is not None
        self.mqtt_factory = mqtt_factory
        self.clock = clock
        self.started = False
        self.connected = False
        self.link = None
        self.handoff = None
        self._submits: "queue.Queue" = queue.Queue()

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        """Everything before the loop. Raises ``NoSwarmTopic`` if no topic can be found."""
        cfg = self.cfg
        name, key = load_key(cfg.key_path)
        self.name = name
        if not self._ltms_given and cfg.ltms_server:
            from cli_agent.client import LTMSClient
            self.ltms = LTMSClient(cfg.ltms_server, key_path=cfg.key_path)
        ltms = self.ltms
        if cfg.transport == "direct":
            prefix, source = "", "direct"
        else:
            prefix, source = member_prefix(cfg, ltms.swarm if ltms is not None and not cfg.swarm_topic_id else None)
        self.prefix = prefix
        logging.info("Member: %s, transport %s, topic prefix %s (from %s)", name, cfg.transport, prefix or "-", source)
        self.outbox = Outbox(cfg.outbox_path)
        if cfg.handoff_path:
            from cli_agent.handoff import Handoff
            self.handoff = Handoff(cfg.handoff_path)
            logging.info("Member: signing records handed off by this device's harnesses (%s)", cfg.handoff_path)
        if cfg.transport == "auto" and ltms is None:
            logging.info("Member: no LTMS_SERVER, so no direct fallback; MQTT only")
        now = self.clock()
        self.transport = Transport(cfg.transport, cfg.fallback_after_seconds, now, can_direct=ltms is not None)
        self.direct = DirectLink(ltms, self.outbox, name) if ltms is not None else None

        self.use_mqtt = cfg.transport != "direct"
        mqtt_client = None
        if self.use_mqtt:
            if self.mqtt_factory is None:
                import paho.mqtt.client as mqtt  # imported here so the module imports without paho
                mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name)
            else:
                mqtt_client = self.mqtt_factory(name)
        self.mqtt = mqtt_client

        def publish(topic: str, payload: bytes, retain: bool = False) -> None:
            if not self.use_mqtt:
                return  # records stay in the outbox; DirectLink sends them
            mqtt_client.publish(topic, payload, qos=1, retain=retain)

        self.link = MemberLink(name, key, self.outbox, cfg.state_path, publish, prefix)

        def hello():
            try:
                network = local_addresses()
            except OSError:  # no resolvable hostname: announce without addresses
                network = []
            return hello_topic(name, prefix), encode_hello(name, network)

        self.acks: "queue.Queue[bytes]" = queue.Queue()
        self.messages: "queue.Queue" = queue.Queue()
        self.fleet_msgs: "queue.Queue" = queue.Queue()
        self.pinged = threading.Event()
        self.resend_wanted = threading.Event()
        connected_cb = make_on_connect(acks_topic(name, prefix), self.resend_wanted, hello, say_subscription(prefix),
                                       ping_topic(name, prefix), fleet_subscription(prefix))
        state_dir = os.path.dirname(os.path.abspath(cfg.state_path))
        fleet_key_cache = os.path.join(state_dir, "fleet-key.pub")

        def fleet_key() -> str | None:
            """The fleet's public key: cached beside the state, fetched from LTMS if missing (#306)."""
            try:
                with open(fleet_key_cache, encoding="utf-8") as f:
                    return f.read().strip() or None
            except OSError:
                pass
            k = self.env.get("FLEET_PUBLIC_KEY") or (ltms.fleet_key() if ltms is not None else None)
            if k:
                with open(fleet_key_cache, "w", encoding="utf-8") as f:
                    f.write(k)
            return k
        leader_cache = {"at": -1e9, "key": None}

        def leader_key() -> str | None:
            """The current swarm leader's public key (#310), from GET /api/swarm, kept for 10 minutes."""
            if ltms is None:
                return None
            if self.clock() - leader_cache["at"] > 600:
                swarm = ltms.swarm()
                leader_cache.update(at=self.clock(), key=((swarm or {}).get("leader") or {}).get("public_key"))
            return leader_cache["key"]

        from cli_agent.directives import DirectiveHandler, follow_by_default
        self.directives = DirectiveHandler(self.link, fleet_key, os.path.join(state_dir, "directives.json"),
                                           decide=self.decide or follow_by_default, leader_key=leader_key)

        def on_connect(client, userdata, flags, reason_code, properties) -> None:
            self.connected = True
            self.transport.on_connect()
            connected_cb(client, userdata, flags, reason_code, properties)

        def on_disconnect(client, userdata, *args) -> None:
            self.connected = False
            self.transport.on_disconnect(self.clock())

        if self.use_mqtt:
            mqtt_client.on_connect = on_connect
            mqtt_client.on_disconnect = on_disconnect
            mqtt_client.on_message = make_on_message(self.acks, self.messages, self.pinged, self.fleet_msgs)
            if cfg.mqtt_username is not None:
                mqtt_client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password)
            mqtt_client.connect_async(cfg.mqtt_host, cfg.mqtt_port)
            mqtt_client.loop_start()

        self.started_at = now
        self.introducer = None
        if ltms is not None:
            from cli_agent.introductions import Introducer
            self.introducer = Introducer(name, os.path.join(state_dir, "introduced.json"),
                                         make_intro_sender(self.link, cfg.transport == "direct"),
                                         reply_policy=cfg.intro_reply)
        self.next_intro_check = now
        self.next_heartbeat = now + cfg.heartbeat_seconds
        self.next_resend = now + cfg.resend_seconds
        self.next_ping_poll = now
        self.started = True

    # ------------------------------------------------------------------ one pass
    def _on_swarm_message(self, rec: dict) -> None:
        log_message(rec)
        if self.introducer is not None:
            self.introducer.on_message(rec, self.clock())
        if self.on_message_hook is not None:
            try:
                self.on_message_hook(rec)
            except Exception:  # noqa: BLE001 - behaviour code must not stop the member
                logging.exception("Member: on_message failed")

    def _run_submits(self) -> None:
        while True:
            try:
                fn, fut = self._submits.get_nowait()
            except queue.Empty:
                return
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(self.link))
            except Exception as exc:  # noqa: BLE001 - handed back to the caller
                fut.set_exception(exc)

    def _pump_handoff(self) -> None:
        if self.handoff is None:
            return
        try:
            n = self.handoff.pump(self.link)
            if n:
                logging.info("Member: signed %d handed-off record(s)", n)
        except Exception:  # noqa: BLE001 - a harness's queue must not stop the member
            logging.exception("Member: handoff failed")

    def step(self) -> None:
        """One pass of the member loop. Non-blocking; main thread only."""
        if not self.started:
            raise RuntimeError("MemberNode.step() before start()")
        cfg, link = self.cfg, self.link
        drain_acks(link, self.acks)
        drain_messages(self.messages, self.name, self.prefix, self._on_swarm_message)
        drain_directives(self.fleet_msgs, self.directives)
        self._run_submits()
        self._pump_handoff()
        if self.resend_wanted.is_set():
            self.resend_wanted.clear()
            link.resend_pending()
        now = self.clock()
        if now >= self.next_resend:
            # Resend anything unacked: covers a hub restart while we stayed
            # connected. Duplicates are harmless (hub and LTMS both dedupe).
            link.resend_pending()
            self.next_resend = now + cfg.resend_seconds
        if self.introducer is not None:
            if now >= self.next_intro_check:
                self.next_intro_check = now + cfg.intro_check_seconds
                try:
                    self.introducer.check(self.ltms.swarm())
                except Exception:  # noqa: BLE001 - an introduction must not stop the member
                    logging.exception("Member: introduction failed")
            self.introducer.due(now)
        if self.ltms is not None and now >= self.next_ping_poll:
            self.next_ping_poll = now + cfg.ping_poll_seconds
            if self.name in self.ltms.pings():
                self.pinged.set()
        if self.pinged.is_set():
            self.pinged.clear()
            heartbeat(link, self.started_at, now, ping=True)
            logging.info("Member: answered an admin ping")
        elif now >= self.next_heartbeat:
            heartbeat(link, self.started_at, now)
            self.next_heartbeat = now + cfg.heartbeat_seconds
        if self.poll is not None:
            try:
                self.poll(link, now)
            except Exception:  # noqa: BLE001 - a source's bug must not stop the member
                logging.exception("Member: poll failed")
        if self.direct is not None and self.transport.choose(self.clock()) == "direct":
            self.direct.flush()

    # ------------------------------------------------------------------ other threads
    def submit(self, fn) -> Future:
        """Run ``fn(link)`` inside the next ``step()``; returns a Future with its result.

        Safe from any thread. Before ``start()`` or after ``stop()`` the Future fails
        at once with ``RuntimeError``: nothing can be signed then.
        """
        fut: Future = Future()
        if not self.started:
            fut.set_exception(RuntimeError("the member isn't running: nothing can be signed"))
            return fut
        self._submits.put((fn, fut))
        return fut

    def status(self) -> dict:
        if not self.started:
            return {"running": False}
        return {"running": True, "member": self.name, "prefix": self.prefix or "-", "mqtt": self.connected,
                "transport": self.transport.choose(self.clock()), "head_seq": self.link.head["seq"],
                "unacked": len(self.outbox.pending(self.name, 1000))}

    # ------------------------------------------------------------------ stop
    def stop(self) -> None:
        """Run what was submitted, send once directly if that's the transport, disconnect, close."""
        if not self.started:
            return
        self.started = False
        try:
            self._run_submits()
            self._pump_handoff()
            if self.direct is not None and self.transport.choose(self.clock()) == "direct":
                try:
                    self.direct.flush()
                except Exception:  # noqa: BLE001 - best effort on the way out; the outbox keeps them
                    logging.exception("Member: last direct send failed")
        finally:
            while True:  # anything submitted after the last run fails rather than hanging
                try:
                    _fn, fut = self._submits.get_nowait()
                except queue.Empty:
                    break
                if fut.set_running_or_notify_cancel():
                    fut.set_exception(RuntimeError("the member stopped"))
            if self.use_mqtt and self.mqtt is not None:
                try:
                    self.mqtt.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self.mqtt.loop_stop()
            if self.handoff is not None:
                self.handoff.close()
            self.outbox.close()


def main(poll=None) -> None:
    """Run the member. ``poll(link, now)``, if given, is called once a loop (about every
    second) so a program built on the member (a robot sidecar, #312) can sign its own
    records into the same chain and transport. An exception in it is logged, never fatal.
    """
    cfg = load_config(os.environ)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    node = MemberNode(cfg, poll=poll)
    try:
        node.start()
    except NoSwarmTopic as exc:
        raise SystemExit(f"Member: {exc}") from None

    running = True

    def stop(signum, frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            node.step()
            # Sleep up to 1 second, waking early if asked to stop.
            deadline = time.monotonic() + 1.0
            while running and time.monotonic() < deadline:
                time.sleep(min(0.25, deadline - time.monotonic()))
    finally:
        node.stop()


if __name__ == "__main__":
    main()
