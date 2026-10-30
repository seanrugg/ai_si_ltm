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
from cli_agent.mqtt_messages import acks_topic, agent_from_topic, decode_record, encode_ack
from cli_agent.outbox import Outbox
from cli_agent.replicator import replicate_once


class Hub:
    def __init__(self, client, outbox: Outbox, publish, swarm: str) -> None:
        self.client = client
        self.outbox = outbox
        self.publish = publish
        self.swarm = swarm
        self.lock = threading.Lock()
        self.dropped = 0
        self.stuck: dict = {}

    def on_record(self, topic: str, payload: bytes) -> bool:
        try:
            agent = agent_from_topic(topic, self.swarm)
            rec = decode_record(payload, agent)
            with self.lock:
                return self.outbox.add(rec)
        except ValueError as exc:  # bad topic, bad JSON, agent mismatch, seq conflict
            self.dropped += 1
            logging.warning("Hub: dropped record on %s: %s", topic, exc)
            return False

    def tick(self, batch_size: int = 100) -> dict | None:
        try:
            with self.lock:
                result = replicate_once(self.client, self.outbox, batch_size)
        except (LTMSError, httpx.HTTPError) as exc:
            logging.warning("Hub: tick failed: %s", exc)
            return None  # publish nothing; the outbox is untouched
        for name, head in result["heads"].items():
            self.publish(acks_topic(name, self.swarm), encode_ack(head), retain=True)
        self.stuck = dict(result["errors"])
        return result
