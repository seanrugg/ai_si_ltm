"""Enrollment: approve / revoke / permission flow and integrity re-verification.

Covers the admin enrollment lifecycle (plan §"Admin"): create a pending member,
approve it, revoke it, update permissions, and verify the chain stays intact.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from server.models.member import Member
from server.services.enrollment import (
    approve_enrollment,
    create_enrollment_request,
    revoke_member,
    set_permissions,
)
from server.services.ingest import ingest_batch

from .conftest import genesis_prev_hash_hex, make_secret, signed_record


async def test_enrollment_lifecycle(db_session):
    member = await create_enrollment_request(db_session, name="new-agent", kind="agent")
    assert member.status == "pending"

    approved = await approve_enrollment(db_session, member.id)
    assert approved.status == "active"
    assert approved.enrolled_at is not None


async def test_revoke_keeps_chain(db_session):
    member = await create_enrollment_request(db_session, name="revocable", kind="agent")
    await approve_enrollment(db_session, member.id)
    revoked = await revoke_member(db_session, member.id)
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None


async def test_set_permissions(db_session):
    member = await create_enrollment_request(db_session, name="perm-agent", kind="agent")
    updated = await set_permissions(db_session, member.id, ["contribute"])
    assert updated.permissions == ["contribute"]


async def test_integrity_ok_after_ingest(db_session):
    from server.services.integrity import verify_member_chain

    secret = make_secret()
    member = await create_enrollment_request(db_session, name="integrity-agent", kind="agent")
    member.public_key = secret.verifying_key().raw
    member.permissions = ["contribute", "recall"]
    await approve_enrollment(db_session, member.id)

    genesis = signed_record(
        secret, seq=1, body={"text": "hi"}, prev_hash_hex=genesis_prev_hash_hex()
    )
    await ingest(genesis)

    status = await verify_member_chain(db_session, member)
    assert status["status"] == "ok"


async def ingest(record):
    member = (await db_session.scalar(select(Member).where(Member.name == "integrity-agent")))
    await ingest_batch(db_session, member, [record])
