"""MQTT message helpers: topics, record and ack encoding."""
import json

import pytest

from cli_agent.mqtt_messages import (
    DEFAULT_SWARM,
    acks_topic,
    agent_from_topic,
    decode_ack,
    decode_record,
    encode_ack,
    encode_record,
    records_topic,
)
from ltms_contract import GENESIS_HASH, build_record, generate_key


def test_topic_builders():
    assert records_topic("z3-r0") == "echo-base/z3-r0/records"
    assert records_topic("z3-r0", "swarm-2") == "swarm-2/z3-r0/records"
    assert acks_topic("z3-r0") == "echo-base/z3-r0/acks"
    assert acks_topic("z3-r0", "swarm-2") == "swarm-2/z3-r0/acks"
    assert DEFAULT_SWARM == "echo-base"


def test_agent_from_topic():
    assert agent_from_topic("echo-base/z3-r0/records") == "z3-r0"
    assert agent_from_topic("echo-base/z3-r0/records", "echo-base") == "z3-r0"
    with pytest.raises(ValueError):
        agent_from_topic("other-swarm/z3-r0/records")  # wrong swarm
    with pytest.raises(ValueError):
        agent_from_topic("echo-base/z3-r0")  # two parts
    with pytest.raises(ValueError):
        agent_from_topic("echo-base/z3-r0/acks")  # acks topic


def test_record_round_trip():
    key = generate_key()
    rec = build_record(
        key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event", summary="hi"
    )
    assert decode_record(encode_record(rec), "z3-r0") == rec


def test_decode_record_rejects():
    key = generate_key()
    rec = build_record(
        key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event", summary="hi"
    )
    with pytest.raises(ValueError):
        decode_record(encode_record(rec), "other-agent")  # mismatched agent
    with pytest.raises(ValueError):
        decode_record(b"not json", "z3-r0")  # non-JSON bytes
    with pytest.raises(ValueError):
        decode_record(b"[1, 2, 3]", "z3-r0")  # JSON list


def test_ack_round_trip():
    head = {"last_ack_seq": 7, "last_hash": "a" * 64}
    assert decode_ack(encode_ack(head)) == head


def test_decode_ack_rejects():
    with pytest.raises(ValueError):
        decode_ack(json.dumps({"last_ack_seq": 7}).encode("utf-8"))  # missing key
    with pytest.raises(ValueError):
        decode_ack(
            json.dumps({"last_ack_seq": "7", "last_hash": "a" * 64}).encode("utf-8")
        )  # string seq
