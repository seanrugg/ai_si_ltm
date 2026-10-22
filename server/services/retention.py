"""Retention jobs. Headers are never deleted; only payloads, expired auth state
and old audit rows are.

Rollups (summary records replacing old event payloads) need the server to sign
its own ``system`` records; that comes with the server identity in a later
phase. Until then each purge is recorded in the audit log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AdminSession, AuditLog, AuthChallenge, Record, RecordPayload, Token

KEEP_TAG = "keep"  # records with this tag are never purged


async def purge_payloads(session: AsyncSession, rules, now: datetime | None = None) -> int:
    """Delete the payloads of records matched by each rule (see ``PayloadRule``).

    Each rule runs on its own, so a record matched by several rules goes at the
    shortest age. Returns the number of payloads deleted; each rule that
    deleted something is recorded in the audit log.
    """
    from server.services.recall import _json_list_contains  # shared JSON-array test

    now = now or datetime.now(timezone.utc)
    total = 0
    for rule in rules:
        cutoff = now - timedelta(days=rule.days)
        # tags may be SQL NULL; on Postgres "NOT (NULL @> ...)" is NULL, so test it explicitly
        not_kept = or_(Record.tags.is_(None), ~_json_list_contains(session, Record.tags, KEEP_TAG))
        ids = select(Record.id).where(Record.ts < cutoff, not_kept)
        if rule.type is not None:
            ids = ids.where(Record.type == rule.type)
        if rule.tag is not None:
            ids = ids.where(_json_list_contains(session, Record.tags, rule.tag))
        result = await session.execute(delete(RecordPayload).where(RecordPayload.record_id.in_(ids)))
        count = result.rowcount or 0
        if count:
            session.add(AuditLog(actor="system", action="retention_purge",
                                 detail={"what": "payloads", "type": rule.type, "tag": rule.tag,
                                         "older_than": cutoff.isoformat(), "count": count}))
        total += count
    return total


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
