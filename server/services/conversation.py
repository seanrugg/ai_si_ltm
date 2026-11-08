"""Conversation queries: the swarm view and the group member view.

The swarm a record belongs to comes from ``record_provenance.swarm_id`` (the
author's swarm when it was written), never from current membership.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Member, Record, RecordProvenance, Swarm, SwarmMember
from server.services import recall
from server.services.errors import ServiceError


async def swarm_conversation(
    session: AsyncSession,
    *,
    swarm: str,
    limit: int,
    before: tuple[datetime, int] | None = None,
) -> list[dict]:
    """Communication timeline for a swarm, oldest first.

    Includes records whose provenance swarm is the chosen swarm, plus messages
    from outside the swarm addressed to one of its current members. ``swarm="free"``
    returns free-agent communications only (provenance swarm null).
    Each record gains ``from_outside`` and ``relayed_by``.
    """
    want_free = swarm == "free"

    if want_free:
        swarm_id = None
    else:
        row = await session.scalar(select(Swarm).where(Swarm.name == swarm))
        if row is None:
            raise ServiceError(404, "not_found", "swarm not found")
        swarm_id = row.id

    # Current (open) members of the chosen swarm, used to detect outside
    # messages addressed to the swarm. Note: membership at write time would be
    # more precise; current membership is the sanctioned simplification.
    swarm_member_names: list[str] = []
    if not want_free and swarm_id is not None:
        rows = await session.execute(
            select(Member.name)
            .join(SwarmMember, SwarmMember.member_id == Member.id)
            .where(SwarmMember.swarm_id == swarm_id)
            .where(SwarmMember.left_at.is_(None))
        )
        swarm_member_names = [name for (name,) in rows]

    if swarm_member_names:
        addressed = or_(*[recall._json_list_contains(session, Record.peers, name)
                          for name in swarm_member_names])
    else:
        addressed = false()

    stmt = select(Record).where(Record.type == "communication")
    stmt = stmt.outerjoin(RecordProvenance, RecordProvenance.record_id == Record.id)

    if want_free:
        # Free agents: provenance swarm null (or no provenance row at all).
        stmt = stmt.where(RecordProvenance.swarm_id.is_(None))
    else:
        inside = RecordProvenance.swarm_id == swarm_id
        not_from_swarm = or_(RecordProvenance.swarm_id.is_(None),
                             RecordProvenance.swarm_id != swarm_id)
        stmt = stmt.where(inside | (not_from_swarm & addressed))

    if before is not None:
        before_ts, before_id = before
        stmt = stmt.where(or_(Record.ts < before_ts,
                              and_(Record.ts == before_ts, Record.id < before_id)))

    stmt = stmt.order_by(Record.ts.desc(), Record.id.desc()).limit(limit)
    rows = list(await session.scalars(stmt))[::-1]  # oldest first

    return await _attach_conversation(session, rows, swarm_id, want_free)


async def member_conversation(
    session: AsyncSession,
    member_a: str,
    member_b: str,
    limit: int,
) -> list[dict]:
    """Communications between two members, oldest first (unchanged behavior)."""
    peers = recall._json_list_contains
    stmt = (
        select(Record)
        .where(Record.type == "communication")
        .where(or_(
            (Record.agent == member_a) & peers(session, Record.peers, member_b),
            (Record.agent == member_b) & peers(session, Record.peers, member_a),
        ))
        .order_by(Record.ts.desc(), Record.id.desc())
        .limit(limit)
    )
    rows = list(await session.scalars(stmt))[::-1]
    return await _attach_conversation(session, rows, None, False)


async def group_conversation(
    session: AsyncSession,
    members: list[str],
    limit: int,
) -> list[dict]:
    """Communication authored by any listed member whose peers include another
    listed member. Two members behaves like member_conversation. 422 above 16."""
    if len(members) > 16:
        raise ServiceError(422, "too_many_members", "at most 16 members")
    if len(members) < 2:
        return []

    conds = []
    for m in members:
        others = [o for o in members if o != m]
        if not others:
            continue
        conds.append(
            (Record.agent == m)
            & or_(*[recall._json_list_contains(session, Record.peers, o) for o in others])
        )
    stmt = (
        select(Record)
        .where(Record.type == "communication")
        .where(or_(*conds))
        .order_by(Record.ts.desc(), Record.id.desc())
        .limit(limit)
    )
    rows = list(await session.scalars(stmt))[::-1]
    return await _attach_conversation(session, rows, None, False)


async def _attach_conversation(
    session: AsyncSession,
    rows: list[Record],
    swarm_id: str | None,
    want_free: bool,
) -> list[dict]:
    """Add payloads and the ``from_outside`` / ``relayed_by`` fields to wire records."""
    if not rows:
        return []
    prov_rows = await session.execute(
        select(RecordProvenance, Member.name)
        .outerjoin(Member, Member.id == RecordProvenance.relay_member_id)
        .where(RecordProvenance.record_id.in_([r.id for r in rows]))
    )
    prov_by_id = {rp.record_id: (rp.swarm_id, relay_name) for rp, relay_name in prov_rows}
    payloads = await recall.fetch_payloads(session, rows)

    records: list[dict] = []
    for r in rows:
        prov_swarm_id, relay_name = prov_by_id.get(r.id, (None, None))
        wire = recall.to_wire(r, payloads.get(r.id))
        if want_free:
            wire["from_outside"] = False
        else:
            wire["from_outside"] = bool(
                swarm_id is not None and (prov_swarm_id is None or prov_swarm_id != swarm_id))
        wire["relayed_by"] = relay_name
        wire["id"] = r.id  # database id, used as the ``before`` paging cursor
        records.append(wire)
    return records
