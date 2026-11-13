"""Scheduled maintenance (APScheduler, in-process)."""
from __future__ import annotations

import httpx
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from server.services import cognition, dataset_uploads, enrollment, lrs, network, reference_sources, retention

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
    # Data retention settings are resolved on every run, so dashboard changes
    # apply at the next run without a restart (issue #155).
    sched.add_job(run, "cron", hour=7, minute=0,  # 03:00 US Eastern
                  args=("purge_payloads", retention.run_payload_purge, cfg))
    sched.add_job(run, "cron", day_of_week="mon", hour=8, minute=0,
                  args=("prune_audit_log", retention.run_audit_prune, cfg))
    sched.add_job(run, "cron", hour=7, minute=15,  # nightly, after the payload purge
                  args=("prune_member_addresses", network.run_prune, cfg))
    # Unfinished dataset uploads older than 24 h (#245).
    sched.add_job(run, "cron", hour=7, minute=20, args=("expire_dataset_uploads", dataset_uploads.purge_stale, cfg))
    if cfg.reference_refresh_hours > 0:
        # Built-in reference sources an admin has imported (#351): new publications become pending versions.
        sched.add_job(run, "interval", hours=cfg.reference_refresh_hours,
                      args=("reference_refresh", reference_sources.refresh_all, cfg))
    if cfg.enable_scheduler:
        # Where each robot's decisions are made (#199): nightly, after the purge.
        sched.add_job(run, "cron", hour=7, minute=30, args=("observe_cognition", cognition.observe_all))
        # The forwarder's own HTTP client, with a timeout, one per scheduler.
        http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        sched.add_job(run, "interval", seconds=cfg.lrs_forward_seconds,
                      args=("lrs_forward", lrs.forward_once, cfg, http))
        app.state.lrs_forward_http = http
    # Hourly maintenance: aggregate auto_denied refusions into one audit row,
    # and expire pending enrollments older than the TTL.
    sched.add_job(run, "interval", hours=1,
                  args=("enroll_auto_denied", enrollment.enroll_auto_denied))
    sched.add_job(run, "interval", hours=1,
                  args=("enroll_expire_pending", enrollment.enroll_expire_pending,
                        cfg.pending_enrollment_ttl_days))
    sched.start()
    return sched
