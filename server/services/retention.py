"""Retention jobs. Headers are never deleted; only payloads, expired auth state
and old audit rows are.

Rollups (summary records replacing old event payloads) need the server to sign
its own ``system`` records; that comes with the server identity in a later
phase. Until then each purge is recorded in the audit log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AdminSession, AuditLog, AuthChallenge, Record, RecordPayload, Token

PURGEABLE_TYPES = ("event", "perception")


async def purge_event_payloads(session: AsyncSession, days: int, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    ids = select(Record.id).where(Record.type.in_(PURGEABLE_TYPES), Record.ts < cutoff)
    result = await session.execute(delete(RecordPayload).where(RecordPayload.record_id.in_(ids)))
    count = result.rowcount or 0
    if count:
        session.add(AuditLog(actor="system", action="retention_purge",
                             detail={"what": "payloads", "types": list(PURGEABLE_TYPES),
                                     "older_than": cutoff.isoformat(), "count": count}))
    return count


async def purge_expired_auth(session: AsyncSession, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    grace = now - timedelta(days=1)  # keep revoked/expired tokens a day for forensics
    t = await session.execute(delete(Token).where(Token.expires_at < grace))
    c = await session.execute(delete(AuthChallenge).where(AuthChallenge.expires_at < now))
    s = await session.execute(delete(AdminSession).where(AdminSession.expires_at < grace))
    return {"tokens": t.rowcount or 0, "challenges": c.rowcount or 0, "admin_sessions": s.rowcount or 0}


async def prune_audit_log(session: AsyncSession, days: int, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    result = await session.execute(delete(AuditLog).where(AuditLog.ts < now - timedelta(days=days)))
    return result.rowcount or 0
