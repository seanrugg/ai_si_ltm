"""Full-chain re-verification for a member (admin integrity view).

Integrity re-verifies every header of a member's chain from genesis against the
persisted payload, recomputing ``body_hash`` and the chain links and checking
each signature. This is how the admin dashboard detects a tampered chain — an
edit to a stored ``recordpayload`` changes its ``body_hash`` and breaks the link;
an edit to a stored header breaks the ``hash`` -> ``prev_hash`` chain.

The result is a per-member status summary: ``"ok"`` when every header verifies,
or ``"broken"`` at the first header that fails, with the failing ``seq`` and
reason.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.member import Member
from server.models.record import RecordHeader
from server.models.recordpayload import RecordPayload
from server.security.key import Ed25519PublicKey
from server.services.chain import GENESIS_PREV_HASH, ChainError, verify_chain

# Verified in a single pass; these windows are only used to bound how far in the
# future/past a historical record may sit. For re-verification of old records we
# allow a generous skew so archived history is never flagged as stale.
_REPLAY_SEC = 3600 * 24 * 30
_SKEW_SEC = 3600 * 24


async def verify_member_chain(
    session: AsyncSession, member: Member, *, now: datetime | None = None
) -> dict:
    """Re-verify a member's full chain; return ``{"status", "seq", "code", ...}``.

    ``status`` is ``"ok"`` (all headers verified) or ``"broken"`` (a header
    failed), with the first failing ``seq`` and the :class:`ChainError` ``code``
    when broken.
    """
    now = now or datetime.now(timezone.utc)
    verifying = Ed25519PublicKey(member.public_key)

    stmt = (
        select(RecordPayload, RecordHeader)
        .where(RecordHeader.member_id == member.id)
        .order_by(RecordHeader.seq.asc())
    )
    rows = await session.execute(stmt)

    prev_header = None
    for payload, header in rows.all():
        prev_view: dict | None = (
            {"seq": prev_header.seq, "hash": prev_header.hash}
            if prev_header is not None
            else None
        )
        try:
            verify_chain(
                payload.body,
                header={
                    "seq": header.seq,
                    "ts": header.ts,
                    "type": header.type,
                    "body_hash": header.body_hash,
                    "sig": header.sig,
                    "hash": header.hash,
                    "prev_hash": header.prev_hash,
                    "agent": header.agent,
                },
                prev_header=prev_view,
                verifying=verifying,
                now=now,
                replay_sec=_REPLAY_SEC,
                skew_sec=_SKEW_SEC,
                seq=header.seq,
                type_=header.type,
                agent=header.agent,
            )
        except ChainError as exc:
            return {"status": "broken", "member": str(member.id), "seq": header.seq, "code": exc.code}
        prev_header = header

    return {"status": "ok", "member": str(member.id)}


async def integrity_summary(session: AsyncSession) -> list[dict]:
    """Return the integrity status of every member in the system."""
    stmt = select(Member)
    members = await session.scalars(stmt)
    results = []
    for member in members:
        results.append(await verify_member_chain(session, member))
    return results
