"""Admin ping (#356): ask a member for a heartbeat and see whether it answers.

Pull only: LTMS never connects out. The member (or its swarm's hub, for members
on MQTT) fetches pending pings with ``GET /api/members/me/pings``; fetching
marks them delivered. The answer is the first record LTMS receives from that
member after the request (normally a heartbeat with ``"ping": true``).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from server.models import AuditLog, Member, Record, SwarmMember
from server.services import swarms
from server.services.errors import ServiceError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pending(m: Member) -> bool:
    return m.ping_requested_at is not None and (
        m.ping_delivered_at is None or m.ping_delivered_at < m.ping_requested_at)


async def _member(session, name: str) -> Member:
    m = await session.scalar(select(Member).where(Member.name == name))
    if m is None:
        raise ServiceError(404, "not_found", f"no member {name!r}")
    return m


async def request(session, name: str, *, admin: str) -> dict:
    m = await _member(session, name)
    if m.status != "active":
        raise ServiceError(409, "not_active", f"{name} is {m.status}; only an active member can answer")
    m.ping_requested_at, m.ping_requested_by, m.ping_delivered_at = _now(), admin, None
    session.add(AuditLog(actor=admin, action="member_ping", detail={"member": name}))
    await session.flush()
    return await status(session, name)


async def status(session, name: str) -> dict:
    m = await _member(session, name)
    out = {"member": name, "requested_at": None, "requested_by": None, "delivered_at": None,
           "answered_at": None, "answer": None}
    if m.ping_requested_at is None:
        return out
    out.update(requested_at=m.ping_requested_at.isoformat(), requested_by=m.ping_requested_by,
               delivered_at=m.ping_delivered_at.isoformat() if not _pending(m) and m.ping_delivered_at else None)
    rec = await session.scalar(
        select(Record).where(Record.member_id == m.id, Record.received_at >= m.ping_requested_at)
        .order_by(Record.received_at, Record.id).limit(1))
    if rec is not None:
        out["answered_at"] = rec.received_at.isoformat()
        out["answer"] = {"agent": rec.agent, "seq": rec.seq, "type": rec.type, "summary": rec.summary,
                         "ts": rec.ts.isoformat()}
    return out


async def take_for(session, member_id) -> list[str]:
    """Pending pings this caller should deliver: itself, and (for a hub) its swarm's members."""
    me = await session.get(Member, member_id)
    wanted = [me] if _pending(me) else []
    swarm_id = await swarms.hub_swarm_id(session, me)
    if swarm_id is not None:
        others = await session.scalars(
            select(Member).join(SwarmMember, SwarmMember.member_id == Member.id)
            .where(SwarmMember.swarm_id == swarm_id, SwarmMember.left_at.is_(None), Member.id != me.id,
                   Member.ping_requested_at.is_not(None)))
        wanted += [m for m in others if _pending(m)]
    now = _now()
    for m in wanted:
        m.ping_delivered_at = now
    await session.flush()
    return [m.name for m in wanted]
