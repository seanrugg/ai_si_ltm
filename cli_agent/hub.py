"""Hub core: MQTT records in, replicator tick, acks out.

Accepts records arriving over MQTT into the hub's Outbox, runs the replicator
on a timer, and publishes each member's LTMS-acknowledged head back on its
acks topic. MQTT delivers messages on its own thread, so the Outbox (a single
sqlite3 connection) is shared between threads and guarded by a lock.
"""
from __future__ import annotations

import logging
import threading

import httpx

from cli_agent.client import LTMSError
from cli_agent.mqtt_messages import (
    acks_topic, agent_from_hello_topic, agent_from_topic, decode_hello, decode_record, encode_ack,
)
from cli_agent.outbox import Outbox
from cli_agent.replicator import replicate_once


class HeartbeatTimer:
    """Due at once on start, then every ``interval`` seconds (monotonic clock)."""

    def __init__(self, interval: float, now: float) -> None:
        self.interval = interval
        self.next = now

    def due(self, now: float) -> bool:
        if now < self.next:
            return False
        self.next = now + self.interval
        return True


class Hub:
    def __init__(self, client, outbox: Outbox, publish, swarm: str, name: str | None = None,
                 legacy: str | None = None) -> None:
        self.client = client
        self.name = name  # the hub's own member name, if it signs its own records
        self.outbox = outbox
        self.publish = publish
        self.swarm = swarm  # the topic prefix: the swarm's topic ID (#293)
        # Migration (#293): also serve the old name-based prefix while members move over.
        self.prefixes = [swarm] + ([legacy] if legacy and legacy != swarm else [])
        self.legacy = legacy if legacy and legacy != swarm else None
        self._legacy_logged: set[str] = set()
        self.lock = threading.Lock()
        self.dropped = 0
        self.stuck: dict = {}
        # LAN addresses from members' hello messages (#216), sent to LTMS on the next tick.
        self.pending_network: dict[str, list] = {}
        # Members heard on MQTT (records or hello) since the last heartbeat (#281).
        self.seen: set[str] = set()

    def _agent(self, topic: str, parse) -> str:
        """The agent in ``topic`` under any served prefix; logs once per member still on the legacy one."""
        last = None
        for prefix in self.prefixes:
            try:
                agent = parse(topic, prefix)
            except ValueError as exc:
                last = exc
                continue
            if prefix == self.legacy and agent not in self._legacy_logged:
                self._legacy_logged.add(agent)
                logging.info("Hub: %s still uses the legacy topic prefix %r; move it to %r",
                             agent, self.legacy, self.swarm)
            return agent
        raise last

    def on_record(self, topic: str, payload: bytes) -> bool:
        try:
            agent = self._agent(topic, agent_from_topic)
            rec = decode_record(payload, agent)
            with self.lock:
                self.seen.add(agent)
                return self.outbox.add(rec)
        except ValueError as exc:  # bad topic, bad JSON, agent mismatch, seq conflict
            self.dropped += 1
            logging.warning("Hub: dropped record on %s: %s", topic, exc)
            return False

    def on_hello(self, topic: str, payload: bytes) -> bool:
        """A member announced itself. Its addresses are reported on the next tick. Never raises."""
        try:
            agent = self._agent(topic, agent_from_hello_topic)
            msg = decode_hello(payload, agent)
        except ValueError as exc:
            self.dropped += 1
            logging.warning("Hub: dropped hello on %s: %s", topic, exc)
            return False
        with self.lock:
            self.seen.add(agent)
            if msg.get("network"):
                self.pending_network[agent] = msg["network"]
        return True

    def report_network(self) -> int:
        """Send pending hello addresses to LTMS (PUT /api/relay/network/{agent}). Never raises."""
        with self.lock:
            pending, self.pending_network = self.pending_network, {}
        sent = 0
        for agent, network in pending.items():
            try:
                self.client._call("PUT", f"/api/relay/network/{agent}", json={"addresses": network})
                sent += 1
            except (LTMSError, httpx.HTTPError) as exc:
                self.dropped += 1
                logging.warning("Hub: network report for %s failed: %s", agent, exc)
        return sent

    def heartbeat(self, link, started_at: float, now: float) -> dict:
        """Sign the hub's own heartbeat into the outbox (#281); the next tick relays it.

        ``link`` is a MemberLink for the hub's own key with a no-op publish: the
        record goes straight into the shared outbox, never onto MQTT.
        """
        with self.lock:
            members_seen, self.seen = len(self.seen), set()
            return link.append(
                "perception",
                summary="heartbeat",
                tags=["telemetry", "internal"],
                payload={"uptime_s": round(now - started_at, 1), "role": "hub",
                         "members_seen": members_seen},
            )

    def tick(self, batch_size: int = 100) -> dict | None:
        self.report_network()
        try:
            with self.lock:
                result = replicate_once(self.client, self.outbox, batch_size)
        except (LTMSError, httpx.HTTPError) as exc:
            logging.warning("Hub: tick failed: %s", exc)
            return None  # publish nothing; the outbox is untouched
        for name, head in result["heads"].items():
            if name == self.name:
                continue  # the hub's own records: nobody on MQTT waits for these acks
            for prefix in self.prefixes:
                self.publish(acks_topic(name, prefix), encode_ack(head), retain=True)
        self.stuck = dict(result["errors"])
        return result
