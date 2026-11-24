"""Admin ping (#356): ask a member for a heartbeat and see whether it answers.

Pull only: LTMS never connects out. The member (or its swarm's hub, for members
on MQTT) fetches pending pings with ``GET /api/members/me/pings``; fetching
marks them delivered. The answer is the first record LTMS receives from that
member after the request (normally a heartbeat with ``"ping": true``).

How a member can answer (#384) is its **answer mode**:

* ``polling``: it runs all the time and checks for pings itself or through its
  hub, so it answers within a minute or two;
* ``on_use``: it runs only when someone uses it (a chat assistant), so it
  answers with its next record, which may be hours away;
* ``none``: it can't answer pings (with a reason); the admin can't ping it;
* ``unknown``: nothing declared and nothing to infer from.

The member declares it in its spec sheet (``software.ping``, ``software.ping_reason``,
#332), or the admin overrides it there. Otherwise LTMS infers it: a member with
regular heartbeats is ``polling``; an ``assistant`` without them is ``on_use``.

Only a record counts as an answer, not any authenticated call: a record proves
the member's own loop runs, not just that its key works.

A hub is handed pings only for its members that may be on the bus: never for an
``on_use`` or ``none`` member, which isn't on MQTT and fetches its own.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from server.models import AuditLog, Member, Record, SwarmMember
from server.services import specs, swarms
from server.services.errors import ServiceError

_UNSET = object()


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


async def _interval(session, m: Member) -> float | None:
    from server.services import fleet_health  # fleet_health imports this module

    return fleet_health.interval_of([b.ts for b in await fleet_health._heartbeats(session, m)])


def declared_mode(m: Member) -> dict | None:
    """The answer mode from the spec sheet (declared or admin), or None."""
    sw = specs.resolve(m)["software"]
    value = sw["value"] or {}
    mode = value.get("ping")
    if mode not in specs.PING_MODES:
        return None
    return {"mode": mode, "source": "admin" if "ping" in sw["admin_fields"] else "declared",
            "reason": value.get("ping_reason") or None}


async def mode_of(session, m: Member, *, interval_s=_UNSET) -> dict:
    """``{"mode", "source", "reason"}``: declared, else inferred, else unknown.

    ``interval_s``: the member's observed heartbeat interval, when the caller has it.
    """
    found = declared_mode(m)
    if found is not None:
        return found
    if interval_s is _UNSET:
        interval_s = await _interval(session, m)
    if interval_s:
        return {"mode": "polling", "source": "inferred", "reason": None}
    if m.kind == "assistant":
        return {"mode": "on_use", "source": "inferred", "reason": None}
    return {"mode": "unknown", "source": None, "reason": None}


async def request(session, name: str, *, admin: str) -> dict:
    m = await _member(session, name)
    if m.status != "active":
        raise ServiceError(409, "not_active", f"{name} is {m.status}; only an active member can answer")
    mode = await mode_of(session, m)
    if mode["mode"] == "none":
        why = mode["reason"] or "its spec sheet says it can't answer pings"
        raise ServiceError(409, "ping_off", f"{name} can't answer pings: {why}")
    m.ping_requested_at, m.ping_requested_by = _now(), admin
    m.ping_delivered_at = m.ping_delivered_by = None
    session.add(AuditLog(actor=admin, action="member_ping", detail={"member": name}))
    await session.flush()
    return await status(session, name)


async def status(session, name: str, *, interval_s=_UNSET) -> dict:
    m = await _member(session, name)
    out = {"member": name, "requested_at": None, "requested_by": None, "delivered_at": None,
           "delivered_by": None, "delivered_via": None, "answered_at": None, "answer": None,
           "mode": await mode_of(session, m, interval_s=interval_s)}
    if m.ping_requested_at is None:
        return out
    delivered = not _pending(m) and m.ping_delivered_at is not None
    out.update(requested_at=m.ping_requested_at.isoformat(), requested_by=m.ping_requested_by,
               delivered_at=m.ping_delivered_at.isoformat() if delivered else None)
    if delivered and m.ping_delivered_by:
        out["delivered_by"] = m.ping_delivered_by
        out["delivered_via"] = "member" if m.ping_delivered_by == name else "hub"
    rec = await session.scalar(
        select(Record).where(Record.member_id == m.id, Record.received_at >= m.ping_requested_at)
        .order_by(Record.received_at, Record.id).limit(1))
    if rec is not None:
        out["answered_at"] = rec.received_at.isoformat()
        out["answer"] = {"agent": rec.agent, "seq": rec.seq, "type": rec.type, "summary": rec.summary,
                         "ts": rec.ts.isoformat()}
    return out


async def take_for(session, member_id) -> list[str]:
    """Pending pings this caller should deliver: itself, and (for a hub) its swarm's
    members that may be on the bus (not ``on_use`` or ``none`` ones)."""
    me = await session.get(Member, member_id)
    wanted = [me] if _pending(me) else []
    swarm_id = await swarms.hub_swarm_id(session, me)
    if swarm_id is not None:
        others = await session.scalars(
            select(Member).join(SwarmMember, SwarmMember.member_id == Member.id)
            .where(SwarmMember.swarm_id == swarm_id, SwarmMember.left_at.is_(None), Member.id != me.id,
                   Member.ping_requested_at.is_not(None)))
        for m in others:
            if _pending(m) and (await mode_of(session, m))["mode"] not in ("on_use", "none"):
                wanted.append(m)
    now = _now()
    for m in wanted:
        m.ping_delivered_at, m.ping_delivered_by = now, me.name
    await session.flush()
    return [m.name for m in wanted]
