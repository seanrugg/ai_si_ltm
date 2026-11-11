"""Removing a swarm (issue #232).

The swarm row stays, for history: records, provenance and audit rows point at
its ID. Its status becomes ``removed``, it leaves the swarm list, and its name
moves aside so a new swarm can use it. What happens to the members is the
admin's choice: they become free agents, or they are revoked from LTMS.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Swarm
from server.services import enrollment, swarms
from server.services.errors import ServiceError

MEMBER_CHOICES = ("free", "remove")
RECORD_CHOICES = ("keep",)  # "purge" arrives with #233


async def remove_swarm(session: AsyncSession, swarm: Swarm, *, confirm, members: str, records: str = "keep",
                       admin: str) -> dict:
    if swarm.status == "removed":
        raise ServiceError(404, "no_swarm", "that swarm was already removed")
    if confirm != swarm.name:
        raise ServiceError(422, "bad_confirm", "type the swarm's name to confirm")
    if members not in MEMBER_CHOICES:
        raise ServiceError(422, "bad_members", "members must be 'free' or 'remove'")
    if records not in RECORD_CHOICES:
        raise ServiceError(422, "bad_records", f"records must be one of {', '.join(RECORD_CHOICES)}")

    now = datetime.now(timezone.utc)
    name = swarm.name
    released = await swarms.release_members(session, swarm, now)
    revoked = 0
    if members == "remove":
        for member in released:
            if member.status == "active":
                await enrollment.revoke(session, member.id, admin=admin, reason=f"swarm {name} removed")
                revoked += 1
    await swarms._move_name(session, swarm, swarms.removed_name(swarm))
    swarm.former_name = name
    swarm.status, swarm.removed_at, swarm.is_home = "removed", now, False
    swarms._audit(session, f"admin:{admin}", "swarm_remove",
                  {"swarm": swarm.id, "name": name, "members": members, "count": len(released),
                   "revoked": revoked, "records": records})
    await session.flush()
    return {"id": swarm.id, "name": name, "status": "removed", "members": members,
            "released": len(released), "revoked": revoked, "records": records}
