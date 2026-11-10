"""Memory wipe: a clean slate for a member or a swarm (issue #157).

A wipe moves the member's recall floor to the newest record in the server, so
from then on it recalls only records written after the wipe, by any author.
Policies stay visible: they are operating rules, not memory. Nothing is
deleted or changed in ``records``; the log of record, other members' view of
this member, and anything already sent to an LRS are untouched. There is no
undo: the floor only moves forward.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Member, Record, SwarmMember
from server.services import swarms
from server.services.errors import ServiceError


async def _newest_record_id(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.max(Record.id))) or 0)


def _wipe(session: AsyncSession, member: Member, floor: int, *, admin: str, swarm: str | None) -> dict:
    # max() keeps the floor from ever moving back, even if records were deleted.
    member.recall_floor_id = max(member.recall_floor_id or 0, floor)
    member.memory_generation = (member.memory_generation or 0) + 1
    detail = {"name": member.name, "floor": member.recall_floor_id, "generation": member.memory_generation}
    if swarm is not None:
        detail["swarm"] = swarm
    session.add(AuditLog(member_id=member.id, actor=f"admin:{admin}", action="memory_wipe", detail=detail))
    return detail


async def wipe_member(session: AsyncSession, member_id: uuid.UUID, *, admin: str, confirm: str | None) -> Member:
    member = await session.get(Member, member_id)
    if member is None:
        raise ServiceError(404, "not_found", "no such member")
    if confirm != member.name:
        raise ServiceError(422, "bad_confirm", "confirm must equal the member's name")
    if member.status != "active":
        raise ServiceError(409, "bad_state", f"member is {member.status}, not active")
    _wipe(session, member, await _newest_record_id(session), admin=admin, swarm=None)
    await session.flush()
    return member


async def wipe_swarm(session: AsyncSession, name: str, *, admin: str, confirm: str | None) -> dict:
    swarm = await swarms.get_swarm(session, name)
    if confirm != swarm.name:
        raise ServiceError(422, "bad_confirm", "confirm must equal the swarm's name")
    members = list(await session.scalars(
        select(Member)
        .join(SwarmMember, SwarmMember.member_id == Member.id)
        .where(SwarmMember.swarm_id == swarm.id, SwarmMember.left_at.is_(None), Member.status == "active")
        .order_by(Member.name)))
    floor = await _newest_record_id(session)
    wiped = [_wipe(session, m, floor, admin=admin, swarm=swarm.name) for m in members]
    await session.flush()
    return {"swarm": swarm.name, "wiped": wiped}
