"""Addresses members use (issue #189): what LTMS sees.

Admin only and never exported to an LRS. LTMS records the address a request
came from after a successful login and on every accepted ingest batch. A
batch relayed by a hub records the hub's address, for the hub only.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Member, MemberAddress, Record, RecordProvenance, Swarm, SwarmMember
from server.services.errors import ServiceError

HISTORY_LIMIT = 20
MAX_REPORTED = 8
MAX_INTERFACE = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_private(ip: str) -> bool | None:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return None


async def record_seen(session: AsyncSession, member: Member, ip: str | None) -> None:
    """Upsert (member, ip, "seen"); audit ``network_change`` when the newest seen IP differs."""
    if not ip or member is None:
        return
    newest = await session.scalar(
        select(MemberAddress).where(MemberAddress.member_id == member.id, MemberAddress.source == "seen")
        .order_by(MemberAddress.last_seen.desc(), MemberAddress.id.desc()).limit(1))
    now = _now()
    row = await session.scalar(select(MemberAddress).where(
        MemberAddress.member_id == member.id, MemberAddress.ip == ip, MemberAddress.source == "seen"))
    if row is None:
        session.add(MemberAddress(member_id=member.id, ip=ip, source="seen", first_seen=now, last_seen=now, count=1))
    else:
        row.last_seen = now
        row.count = (row.count or 0) + 1
    if newest is not None and newest.ip != ip:
        session.add(AuditLog(member_id=member.id, actor="system:network", action="network_change",
                             detail={"source": "seen", "old": newest.ip, "new": ip}))


def _validate_reported(body) -> list[dict]:
    addresses = body.get("addresses") if isinstance(body, dict) else None
    if not isinstance(addresses, list) or len(addresses) > MAX_REPORTED:
        raise ServiceError(422, "bad_network", f"addresses must be a list of at most {MAX_REPORTED}")
    clean: dict[str, dict] = {}
    for a in addresses:
        if not isinstance(a, dict) or not isinstance(a.get("ip"), str):
            raise ServiceError(422, "bad_network", "each address needs an ip")
        try:
            ip = str(ipaddress.ip_address(a["ip"].strip()))
        except ValueError:
            raise ServiceError(422, "bad_network", f"not an IP address: {a['ip'][:64]!r}") from None
        interface = a.get("interface")
        if interface is not None and (not isinstance(interface, str) or len(interface) > MAX_INTERFACE):
            raise ServiceError(422, "bad_network", f"interface is a string of at most {MAX_INTERFACE} characters")
        clean[ip] = {"ip": ip, "interface": interface}
    return list(clean.values())


async def record_reported(session: AsyncSession, member: Member, body, *, source: str) -> list[dict]:
    """Store the LAN addresses a member (``reported``) or its hub (``hub``) reports.

    Unverified: LTMS can't check them. The new set replaces the previous one as
    the current set; old rows keep their ``last_seen`` and age out by pruning.
    A different set from last time writes ``network_change``.
    """
    addresses = _validate_reported(body)
    now = _now()
    existing = {r.ip: r for r in await session.scalars(select(MemberAddress).where(
        MemberAddress.member_id == member.id, MemberAddress.source == source))}
    # The current set is the rows refreshed by the latest report (same last_seen).
    newest = max((r.last_seen for r in existing.values()), default=None)
    previous = sorted(ip for ip, r in existing.items() if newest is not None and r.last_seen == newest)
    for a in addresses:
        row = existing.get(a["ip"])
        if row is None:
            session.add(MemberAddress(member_id=member.id, ip=a["ip"], source=source, interface=a["interface"],
                                      first_seen=now, last_seen=now, count=1))
        else:
            row.last_seen, row.interface, row.count = now, a["interface"], (row.count or 0) + 1
    current = sorted(a["ip"] for a in addresses)
    if current != previous:
        session.add(AuditLog(member_id=member.id, actor="system:network", action="network_change",
                             detail={"source": source, "old": previous, "new": current}))
    return [{**a, "source": source} for a in addresses]


async def prune(session: AsyncSession, days: int, now: datetime | None = None) -> int:
    """Delete address rows not seen for ``days`` days."""
    cutoff = (now or _now()) - timedelta(days=days)
    result = await session.execute(delete(MemberAddress).where(MemberAddress.last_seen < cutoff))
    return result.rowcount or 0


async def run_prune(session: AsyncSession, cfg) -> int:
    return await prune(session, cfg.address_history_days)


def _current_reported(addrs: list[MemberAddress]) -> list[dict]:
    """The latest reported set from each source (#190), newest source first. Unverified."""
    out = []
    for source in ("reported", "hub"):
        rows = [a for a in addrs if a.source == source]
        if not rows:
            continue
        newest = max(a.last_seen for a in rows)
        out += [{"ip": a.ip, "interface": a.interface, "source": a.source, "last_seen": a.last_seen.isoformat()}
                for a in rows if a.last_seen == newest]
    return out


async def describe(session: AsyncSession, *, swarm: str | None = None, member: str | None = None) -> dict:
    """Each member's addresses, for the admin Network view (``?swarm=`` or ``?member=`` narrow it)."""
    stmt = select(Member).where(Member.status != "rejected").order_by(Member.name)
    if member:
        stmt = stmt.where(Member.name == member)
    if swarm:
        s = await session.scalar(select(Swarm).where(Swarm.name == swarm))
        if s is None:
            raise ServiceError(404, "not_found", f"no swarm {swarm!r}")
        stmt = stmt.where(Member.id.in_(select(SwarmMember.member_id).where(
            SwarmMember.swarm_id == s.id, SwarmMember.left_at.is_(None))))
    members = list(await session.scalars(stmt))
    if member and not members:
        raise ServiceError(404, "not_found", f"no member {member!r}")
    ids = [m.id for m in members]
    if not ids:
        return {"members": []}

    roles = {r.member_id: r.role for r in await session.scalars(
        select(SwarmMember).where(SwarmMember.member_id.in_(ids), SwarmMember.left_at.is_(None)))}
    rows = list(await session.scalars(
        select(MemberAddress).where(MemberAddress.member_id.in_(ids))
        .order_by(MemberAddress.last_seen.desc(), MemberAddress.id.desc())))
    by_member: dict = {}
    for r in rows:
        by_member.setdefault(r.member_id, []).append(r)

    # path: "via <hub>" when the member's newest record was relayed by someone else.
    newest = (select(Record.member_id, func.max(Record.id).label("rid"))
              .where(Record.member_id.in_(ids)).group_by(Record.member_id).subquery())
    relays = dict((await session.execute(
        select(newest.c.member_id, RecordProvenance.relay_member_id)
        .join(RecordProvenance, RecordProvenance.record_id == newest.c.rid))).all())
    hub_ids = {v for k, v in relays.items() if v is not None and v != k}
    hub_names = dict((await session.execute(
        select(Member.id, Member.name).where(Member.id.in_(hub_ids)))).all()) if hub_ids else {}

    out = []
    for m in members:
        addrs = by_member.get(m.id, [])
        seen = next((a for a in addrs if a.source == "seen"), None)
        relay = relays.get(m.id)
        out.append({
            "name": m.name,
            "role": roles.get(m.id, "free"),
            "kind": m.kind,
            "path": f"via {hub_names[relay]}" if relay is not None and relay != m.id and relay in hub_names else "direct",
            "seen": {"ip": seen.ip, "private": is_private(seen.ip), "last_seen": seen.last_seen.isoformat()}
            if seen else None,
            "reported": _current_reported(addrs),
            "history": [{"ip": a.ip, "source": a.source, "first_seen": a.first_seen.isoformat(),
                         "last_seen": a.last_seen.isoformat()} for a in addrs[:HISTORY_LIMIT]],
        })
    return {"members": out}
