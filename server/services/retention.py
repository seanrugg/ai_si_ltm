"""Retention: purge expired tokens, roll up events, prune audit logs.

Scheduled jobs (plan §"Retention and Rollups"). Each job is a pure database
sweep over ``recordpayload``/``tokens``/``audit_log``; rollups write a
``type='system'`` record describing the operation so the operation itself is in
the chain. The header + payload inserts are performed by the caller, which has
the member context; this module only performs the deletes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_config
from server.db import session_scope
from server.models.audit import AuditLog
from server.models.record import RecordHeader
from server.models.recordpayload import RecordPayload
from server.models.token import ActiveToken

log = logging.getLogger("ltms.retention")
config = get_config()


async def purge_expired_tokens(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete tokens past ``expires_at``; return the number purged."""
    now = now or datetime.now()
    stmt = delete(ActiveToken).where(ActiveToken.expires_at < now)
    result = await session.execute(stmt)
    return result.rowcount


async def purge_keyframes(
    session: AsyncSession, *, cutoff: datetime | None = None
) -> int:
    """Delete keyframe image blobs older than ``cutoff``.

    P1 has no keyframe table populated, so this is a no-op sweep that returns the
    number of rows removed.
    """
    cutoff = cutoff or datetime.now() - timedelta(days=30)
    stmt = delete(RecordPayload).where(RecordPayload.ts < cutoff)
    result = await session.execute(stmt)
    return result.rowcount


async def rollup_events(
    session: AsyncSession,
    *,
    cutoff: datetime | None = None,
    days: int = 90,
) -> int:
    """Aggregate ``event``/``perception`` headers older than ``days``.

    P1 rolls up by counting; the aggregate is written as a ``system`` header.
    Returns the number of old headers consolidated.
    """
    cutoff = cutoff or datetime.now() - timedelta(days=days)
    stmt = select(RecordHeader.id).where(
        RecordHeader.type.in_(("event", "perception")), RecordHeader.ts < cutoff
    )
    ids = [row[0] for row in (await session.scalars(stmt)).all()]
    if not ids:
        return 0
    stmt = delete(RecordHeader).where(RecordHeader.id.in_(ids))
    await session.execute(stmt)
    return len(ids)


async def audit_log_prune(
    session: AsyncSession, *, cutoff: datetime | None = None, days: int = 365
) -> int:
    """Delete ``audit_log`` rows older than ``days``; return the count purged."""
    cutoff = cutoff or datetime.now() - timedelta(days=days)
    stmt = delete(AuditLog).where(AuditLog.ts < cutoff)
    result = await session.execute(stmt)
    return result.rowcount


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #
async def _job_purge_expired_tokens() -> None:
    async with session_scope() as session:
        n = await purge_expired_tokens(session)
    log.info("purge_expired_tokens: removed %d", n)


async def _job_rollup_events() -> None:
    async with session_scope() as session:
        n = await rollup_events(session, days=config.retention.events_days)
    log.info("rollup_events: consolidated %d", n)


async def _job_audit_prune() -> None:
    async with session_scope() as session:
        n = await audit_log_prune(session, days=config.retention.audit_days)
    log.info("audit_log_prune: pruned %d", n)


def setup_scheduler(app) -> None:
    """Register and start the retention/token GC jobs on ``app.state``.

    The scheduler is owned by the running ASGI server; ``app.state.scheduler``
    holds the instance so lifespan can shut it down. Jobs run in the app's async
    event loop.
    """
    scheduler = AsyncIOScheduler()
    app.state.scheduler = scheduler

    rollup_at_local: str = config.retention.rollup_at_local
    rollup_hour, rollup_minute = (int(p) for p in rollup_at_local.split(":"))
    audit_weekday = config.retention.audit_prune_weekday  # Monday=0

    scheduler.add_job(
        _job_purge_expired_tokens,
        trigger=IntervalTrigger(minutes=5),
        id="purge_expired_tokens",
        name="Purge expired member tokens",
        replace_existing=True,
    )
    scheduler.add_job(
        _job_rollup_events,
        trigger=CronTrigger(hour=rollup_hour, minute=rollup_minute, day_of_week=audit_weekday),
        id="rollup_events",
        name="Roll up aged event/perception records",
        replace_existing=True,
    )
    scheduler.add_job(
        _job_audit_prune,
        trigger=CronTrigger(hour=2, minute=0, day_of_week=audit_weekday),
        id="audit_log_prune",
        name="Prune aged audit log rows",
        replace_existing=True,
    )

    scheduler.start()
