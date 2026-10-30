"""MQTT message helpers: topic names and record/ack encoding.

Pure helpers -- no network code and no MQTT library. The hub and robot
programs call these to build topics and to encode/decode the payloads they
publish and subscribe to.
"""
from __future__ import annotations

import json
from typing import Any

_RECORDS_SUFFIX = "records"
_ACKS_SUFFIX = "acks"


def records_topic(agent: str, swarm: str) -> str:
    """robot -> hub: one signed record per message."""
    return f"{swarm}/{agent}/records"


def acks_topic(agent: str, swarm: str) -> str:
    """hub -> robot: LTMS's acknowledged head for that agent."""
    return f"{swarm}/{agent}/acks"


def agent_from_topic(topic: str, swarm: str) -> str:
    """Extract the agent from a ``<swarm>/<agent>/records`` topic.

    The broker's ACL lets an agent publish only under its own topic, so the
    agent in the topic is trusted here.
    """
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != swarm or parts[2] != _RECORDS_SUFFIX:
        raise ValueError(f"not a records topic for swarm {swarm!r}: {topic!r}")
    return parts[1]


def encode_record(record: dict) -> bytes:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_record(data: bytes, expected_agent: str) -> dict:
    record = json.loads(data)
    if not isinstance(record, dict):
        raise ValueError("record must be a JSON object")
    if record.get("agent") != expected_agent:
        raise ValueError(f"record agent {record.get('agent')!r} != expected {expected_agent!r}")
    seq = record.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ValueError("record['seq'] must be an int (not bool)")
    if not isinstance(record.get("hash"), str):
        raise ValueError("record['hash'] must be a str")
    return record


def encode_ack(head: dict[str, Any]) -> bytes:
    """head = {"last_ack_seq": int, "last_hash": str}; only those two keys."""
    return json.dumps(
        {"last_ack_seq": head["last_ack_seq"], "last_hash": head["last_hash"]},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def decode_ack(data: bytes) -> dict[str, Any]:
    head = json.loads(data)
    if not isinstance(head, dict):
        raise ValueError("ack must be a JSON object")
    if "last_ack_seq" not in head or "last_hash" not in head:
        raise ValueError("ack missing last_ack_seq or last_hash")
    seq = head["last_ack_seq"]
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ValueError("ack['last_ack_seq'] must be an int (not bool)")
    if not isinstance(head["last_hash"], str):
        raise ValueError("ack['last_hash'] must be a str")
    return head
