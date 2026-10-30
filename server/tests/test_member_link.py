"""MemberLink tests: sign, queue, resume, prune on ack."""
from __future__ import annotations

import json

from ltms_contract import GENESIS_HASH, generate_key, verify_record

from cli_agent.member_link import MemberLink
from cli_agent.mqtt_messages import encode_ack, encode_record, records_topic
from cli_agent.outbox import Outbox


def _make_link(tmp_path, name="z3-r0"):
    key = generate_key()
    sent = []

    def publish(topic, payload, retain=False):
        sent.append((topic, payload, retain))

    outbox = Outbox(tmp_path / "out.db")
    link = MemberLink(name, key, outbox, tmp_path / "head.json", publish, "echo-base")
    return key, link, sent, outbox


def test_append_signs_chain_and_publishes(tmp_path):
    key, link, sent, outbox = _make_link(tmp_path)

    rec1 = link.append("thought", payload={"a": 1})
    rec2 = link.append("thought", payload={"a": 2})
    rec3 = link.append("thought", payload={"a": 3})

    assert [r["seq"] for r in (rec1, rec2, rec3)] == [1, 2, 3]
    assert rec1["prev_hash"] == GENESIS_HASH
    assert rec2["prev_hash"] == rec1["hash"]
    assert rec3["prev_hash"] == rec2["hash"]

    assert [topic for topic, _, _ in sent] == [
        records_topic("z3-r0", "echo-base"),
        records_topic("z3-r0", "echo-base"),
        records_topic("z3-r0", "echo-base"),
    ]
    for _, payload, retain in sent:
        assert retain is False
        json.loads(payload)  # valid encoded record

    for rec in (rec1, rec2, rec3):
        verify_record(rec, bytes(key.verify_key))

    # All three are waiting in the outbox until an ack arrives.
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [1, 2, 3]


def test_restart_continues_chain(tmp_path):
    key = generate_key()
    sent, publish = [], lambda topic, payload, retain=False: sent.append((topic, payload, retain))
    outbox = Outbox(tmp_path / "out.db")
    link = MemberLink("z3-r0", key, outbox, tmp_path / "head.json", publish, "echo-base")
    rec1 = link.append("thought")
    rec2 = link.append("thought")
    rec3 = link.append("thought")

    # Restart: a new Outbox and MemberLink on the same files.
    link2 = MemberLink("z3-r0", key, Outbox(tmp_path / "out.db"), tmp_path / "head.json", publish, "echo-base")
    rec4 = link2.append("thought")

    assert rec4["seq"] == 4
    assert rec4["prev_hash"] == rec3["hash"]
    verify_record(rec4, bytes(key.verify_key))


def test_on_ack_prunes_outbox(tmp_path):
    key, link, _sent, outbox = _make_link(tmp_path)
    rec1 = link.append("thought")
    rec2 = link.append("thought")
    rec3 = link.append("thought")

    n = link.on_ack(encode_ack({"last_ack_seq": 2, "last_hash": rec2["hash"]}))
    assert n == 2
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [3]


def test_ack_all_then_restart_continues(tmp_path):
    key, link, _sent, outbox = _make_link(tmp_path)
    rec1 = link.append("thought")
    rec2 = link.append("thought")
    rec3 = link.append("thought")

    # Ack everything: the outbox is now empty.
    assert link.on_ack(encode_ack({"last_ack_seq": 3, "last_hash": rec3["hash"]})) == 3
    assert outbox.pending("z3-r0") == []

    # Restart: with no outbox records, the head comes from the state file.
    link2 = MemberLink("z3-r0", key, Outbox(tmp_path / "out.db"), tmp_path / "head.json", lambda t, p, r=False: None, "echo-base")
    rec4 = link2.append("thought")

    assert rec4["seq"] == 4
    assert rec4["prev_hash"] == rec3["hash"]
    verify_record(rec4, bytes(key.verify_key))


def test_append_survives_publish_failure_and_resend(tmp_path):
    key, link, _sent, outbox = _make_link(tmp_path)
    sent = []
    broken = {"value": True}

    def publish(topic, payload, retain=False):
        if broken["value"]:
            raise RuntimeError("broker down")
        sent.append((topic, payload, retain))

    # Rebuild the link with a failing publisher.
    link = MemberLink("z3-r0", key, outbox, tmp_path / "head.json", publish, "echo-base")

    # Publish raises: append still returns the record and keeps it in the outbox.
    rec = link.append("thought", payload={"x": 1})
    assert rec["seq"] == 1
    assert sent == []
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [1]

    # With a working publisher, resend_pending sends it.
    broken["value"] = False
    assert link.resend_pending() == 1
    assert len(sent) == 1
    assert sent[0][0] == records_topic("z3-r0", "echo-base")
    assert json.loads(sent[0][1]) == rec


def test_resend_stops_at_first_failure(tmp_path):
    key = generate_key()
    sent = []
    will_fail = {"value": True}

    def publish(topic, payload, retain=False):
        if will_fail["value"]:
            raise RuntimeError("broker down")
        sent.append((topic, payload, retain))

    outbox = Outbox(tmp_path / "out.db")
    link = MemberLink("z3-r0", key, outbox, tmp_path / "head.json", publish, "echo-base")

    # Both appends fail to publish; both records sit in the outbox.
    link.append("thought")
    link.append("thought")
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [1, 2]

    # Resend while still broken: record 1's publish fails, so we stop at 0.
    assert link.resend_pending() == 0
    assert sent == []

    # Fix the publisher: resend sends both records in order.
    will_fail["value"] = False
    assert link.resend_pending() == 2
    assert [topic for topic, _, _ in sent] == [
        records_topic("z3-r0", "echo-base"),
        records_topic("z3-r0", "echo-base"),
    ]
