"""Ingest: valid batch accepted, wrong agent / bad hash / gap rejected.

Covers the write path (plan §"Server verify order") through the API: a valid
genesis record is accepted and acked, a batch of two is accepted, a wrong agent
is rejected, and a tampered body is rejected.
"""
from __future__ import annotations

import pytest

from server.security.key import Ed25519SecretKey
from server.services.ingest import ingest_batch

from .conftest import genesis_prev_hash_hex, make_secret, signed_record


def _seed_member(db_session, *, permissions=None, status="active", name="test-member"):
    from server.models.member import Member

    secret = make_secret()
    member = Member(
        name=name,
        kind="agent",
        permissions=permissions if permissions is not None else ["contribute", "recall"],
        status=status,
        public_key=secret.verifying_key().raw,
    )
    db_session.add(member)
    db_session.flush()
    return member, secret


async def test_valid_genesis_record_accepted(db_session, active_member):
    secret = make_secret()
    rec = signed_record(secret, seq=1, body={"text": "hi"}, prev_hash_hex=genesis_prev_hash_hex())
    outcomes = await ingest_batch(db_session, active_member, [rec])
    assert outcomes[0]["status"] == "accepted"
    assert active_member.last_ack_seq == 1


async def test_batch_of_two_accepted(db_session, active_member):
    secret = make_secret()
    genesis = signed_record(secret, seq=1, body={"text": "first"}, prev_hash_hex=genesis_prev_hash_hex())
    second = signed_record(secret, seq=2, body={"text": "second"}, prev_hash_hex=genesis["prev_hash"])
    outcomes = await ingest_batch(db_session, active_member, [genesis, second])
    assert all(o["status"] == "accepted" for o in outcomes)
    assert active_member.last_ack_seq == 2


async def test_wrong_agent_rejected(db_session, active_member):
    secret = make_secret()
    rec = signed_record(
        secret,
        seq=1,
        body={"text": "hi"},
        prev_hash_hex=genesis_prev_hash_hex(),
        agent="other-agent",
    )
    outcomes = await ingest_batch(db_session, active_member, [rec])
    # Wrong agent fails verification (server checks agent matches member.name).
    assert outcomes[0]["status"] == "rejected"
    assert active_member.last_ack_seq == 0


async def test_tampered_body_rejected(db_session, active_member):
    secret = make_secret()
    genesis = signed_record(secret, seq=1, body={"text": "first"}, prev_hash_hex=genesis_prev_hash_hex())
    second = signed_record(
        secret,
        seq=2,
        body={"text": "second"},
        prev_hash_hex=genesis["prev_hash"],
        body_hash_override="b" * 64,
    )
    outcomes = await ingest_batch(db_session, active_member, [genesis, second])
    # Atomic: the whole batch is rolled back.
    assert outcomes[1]["status"] == "rejected"
    assert active_member.last_ack_seq == 0


async def test_gap_in_seq_rejected(db_session, active_member):
    secret = make_secret()
    rec = signed_record(secret, seq=2, body={"text": "skip"}, prev_hash_hex=genesis_prev_hash_hex())
    outcomes = await ingest_batch(db_session, active_member, [rec])
    assert outcomes[0]["status"] == "rejected"
    assert active_member.last_ack_seq == 0


async def test_inactive_member_rejected(db_session):
    _, secret = _seed_member(db_session, status="revoked")
    rec = signed_record(secret, seq=1, body={"text": "hi"}, prev_hash_hex=genesis_prev_hash_hex())
    member = active_member
    from server.models.member import Member

    member.status = "revoked"
    with pytest.raises(Exception):
        await ingest_batch(db_session, member, [rec])


async def test_missing_contribute_permission_rejected(db_session):
    _, secret = _seed_member(db_session, permissions=["recall"])
    rec = signed_record(secret, seq=1, body={"text": "hi"}, prev_hash_hex=genesis_prev_hash_hex())
    with pytest.raises(Exception):
        await ingest_batch(db_session, active_member, [rec])
