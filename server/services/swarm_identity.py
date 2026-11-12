"""A member's swarm, as clients need it (#292): permanent ID, topic ID, current name, role.

The local network (MQTT topics, broker ACLs) addresses a swarm by its
**topic ID**, derived only from the permanent swarm ID, so renaming a swarm
changes nothing on any device. The name is for people.

Topic ID: ``sw-`` + the first 8 hex characters of the swarm ID. If an
**older** swarm (any status, so the answer never changes later) already has
that prefix, the newer swarm uses 12, then 16, ... characters until it's
unique among older swarms. The oldest swarm with a prefix always keeps the
short form, so a swarm's topic ID is fixed when it is created.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Member, Swarm
from server.services.swarms import open_membership

TOPIC_PREFIX = "sw-"
SHORT, STEP = 8, 4


def topic_id_for(swarm_id: str, older_ids) -> str:
    """The topic ID for ``swarm_id``, given the IDs of every swarm created before it."""
    older = [o for o in older_ids if o != swarm_id]
    n = SHORT
    while n < len(swarm_id) and any(o[:n] == swarm_id[:n] for o in older):
        n += STEP
    return TOPIC_PREFIX + swarm_id[:n]


async def topic_id(session: AsyncSession, swarm: Swarm) -> str:
    older = (await session.scalars(
        select(Swarm.id).where(Swarm.created_at < swarm.created_at,
                               Swarm.id.like(swarm.id[:SHORT] + "%")))).all()
    return topic_id_for(swarm.id, older)


async def swarm_of(session: AsyncSession, member: Member) -> dict | None:
    """``{"id", "topic_id", "name", "role"}`` for the member's open membership, or None."""
    row = await open_membership(session, member.id)
    if row is None:
        return None
    swarm = await session.get(Swarm, row.swarm_id)
    if swarm is None:
        return None
    return {"id": swarm.id, "topic_id": await topic_id(session, swarm), "name": swarm.name,
            "role": row.role}
