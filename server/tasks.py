"""Scheduled maintenance (APScheduler, in-process)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from server.services import retention

log = logging.getLogger("ltms.tasks")


def start_scheduler(app) -> AsyncIOScheduler:
    cfg = app.state.config

    async def run(name, fn, *args):
        async with app.state.sessionmaker() as session:
            try:
                result = await fn(session, *args)
                await session.commit()
                log.info("%s: %s", name, result)
            except Exception:
                await session.rollback()
                log.exception("%s failed", name)

    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(run, "interval", minutes=5, args=("purge_expired_auth", retention.purge_expired_auth))
    sched.add_job(run, "cron", hour=7, minute=0,  # 03:00 US Eastern
                  args=("purge_payloads", retention.purge_payloads, cfg.retention_payload_rules))
    sched.add_job(run, "cron", day_of_week="mon", hour=8, minute=0,
                  args=("prune_audit_log", retention.prune_audit_log, cfg.retention_audit_days))
    sched.start()
    return sched
