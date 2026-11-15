"""Counts for the dashboard's navigation badges and Get started checklist (#379).

One light read, polled by the dashboard in the background. The route doesn't
count as admin activity unless the request says so (``X-LTMS-Activity: user``),
like any other background poll (#378).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import LrsEndpoint, Member, Swarm, SwarmMember
from server.services import charter, directives


async def _any(session: AsyncSession, stmt) -> bool:
    return bool(await session.scalar(select(exists(stmt))))


async def setup(session: AsyncSession) -> dict:
    """Where a new fleet stands on the Get started checklist, from server state."""
    charter_published = await charter.current(session) is not None
    swarm_created = await _any(session, select(Swarm.id).where(Swarm.status == "active"))
    hub_appointed = await _any(session, select(SwarmMember.id).join(Swarm, Swarm.id == SwarmMember.swarm_id)
                               .where(SwarmMember.role == "hub", SwarmMember.left_at.is_(None),
                                      Swarm.status == "active"))
    lrs_added = await _any(session, select(LrsEndpoint.id))
    member_approved = await _any(session, select(Member.id).where(Member.enrolled_at.is_not(None)))
    return {"charter_published": charter_published, "swarm_created": swarm_created,
            "hub_appointed": hub_appointed, "lrs_added": lrs_added, "member_approved": member_approved}


async def status(session: AsyncSession, cfg, *, directives_seen: datetime | None = None) -> dict:
    """``as_of`` is the server's clock, so the dashboard can remember when the
    admin last looked at Directives without trusting the browser's clock."""
    members_pending = await session.scalar(select(func.count()).select_from(Member)
                                           .where(Member.status == "pending")) or 0
    lrs_failing = await session.scalar(select(func.count()).select_from(LrsEndpoint).where(
        or_(LrsEndpoint.enabled.is_(False), LrsEndpoint.last_error.is_not(None)))) or 0
    as_of = datetime.now(timezone.utc)
    return {
        "as_of": as_of.isoformat(),
        "members_pending": int(members_pending),
        "charter_overdue": await charter.overdue_count(session, cfg),
        "directive_declines": await directives.declines_since(session, directives_seen),
        "lrs_failing": int(lrs_failing),
        "setup": await setup(session),
    }
