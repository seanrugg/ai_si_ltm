"""APScheduler retention jobs.

The jobs and the scheduler setup live in :mod:`server.services.retention`; this
module re-exports them so the APScheduler dependency stays isolated to one place
and ``from server.tasks import setup_scheduler`` works from the app factory.
"""
from __future__ import annotations

from server.services.retention import (  # noqa: F401
    _job_audit_prune,
    _job_purge_expired_tokens,
    _job_rollup_events,
    setup_scheduler,
)
