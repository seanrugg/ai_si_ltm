"""Swarms: membership, the hub appointment, and who may relay for whom (issue #15).

The swarm's **root key** stays offline with the operator. It signs one kind of
statement, a hub appointment, so a swarm's identity survives replacing the
hub hardware: appoint the new hub, and the old one loses relay rights.

Appointment format (signed on the operator's machine by ``scripts/swarm_key.py``)::

    {"statement": {"v": 1, "action": "appoint_hub", "swarm": "<swarm id>",
                   "hub": "<member name>", "hub_key": "<hub public key hex>",
                   "ts": "<RFC 3339>"},
     "sig": "<hex Ed25519 over SWARM_PREFIX || JCS(statement)>"}

The ``ltms-swarm-v1`` prefix keeps these signatures from ever being valid as a
record or login signature (and the reverse).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from nacl.signing import SigningKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ltms_contract import canonicalize, now_ts, parse_ts
from ltms_contract.record import NAME_RE, verify_signature
from server.models import AuditLog, Member, Swarm, SwarmMember
from server.services.errors import ServiceError

SWARM_PREFIX = b"ltms-swarm-v1\n"
APPOINTMENT_MAX_AGE = timedelta(days=30)
MEMBER_ROLES = ("member", "observer")  # "hub" only through a signed appointment


def swarm_id_for(public_key: bytes) -> str:
    return hashlib.sha256(bytes(public_key)).hexdigest()


def appointment_message(statement: dict) -> bytes:
    return SWARM_PREFIX + canonicalize(statement)


def make_appointment(root_key: SigningKey, *, swarm_id: str, hub: str, hub_key_hex: str,
                     ts: str | None = None) -> dict:
    statement = {"v": 1, "action": "appoint_hub", "swarm": swarm_id, "hub": hub,
                 "hub_key": hub_key_hex.lower(), "ts": ts or now_ts()}
    return {"statement": statement, "sig": root_key.sign(appointment_message(statement)).signature.hex()}


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _audit(session: AsyncSession, actor: str, action: str, detail: dict, member_id=None) -> None:
    session.add(AuditLog(member_id=member_id, actor=actor, action=action, detail=detail))


def _without_relay(member: Member) -> None:
    if "relay" in member.permissions:
        member.permissions = [p for p in member.permissions if p != "relay"]


# ---------------------------------------------------------------- lookups
async def get_swarm(session: AsyncSession, name_or_id: str) -> Swarm:
    swarm = await session.scalar(select(Swarm).where((Swarm.name == name_or_id) | (Swarm.id == name_or_id)))
    if swarm is None:
        raise ServiceError(404, "no_swarm", f"no swarm named {name_or_id!r}")
    return swarm


async def open_membership(session: AsyncSession, member_id: uuid.UUID) -> SwarmMember | None:
    return await session.scalar(
        select(SwarmMember).where(SwarmMember.member_id == member_id, SwarmMember.left_at.is_(None))
    )


async def open_memberships(session: AsyncSession, member_ids) -> dict:
    rows = await session.scalars(
        select(SwarmMember).where(SwarmMember.member_id.in_(list(member_ids)), SwarmMember.left_at.is_(None))
    )
    return {row.member_id: row for row in rows}


async def hub_swarm_id(session: AsyncSession, member: Member) -> str | None:
    """The swarm this member may relay for, or None.

    Needs all of: the ``relay`` permission, an open ``hub`` membership, and an
    active swarm. Revoking the member, the swarm or the appointment ends it.
    """
    if member.status != "active" or "relay" not in member.permissions:
        return None
    row = await open_membership(session, member.id)
    if row is None or row.role != "hub":
        return None
    swarm = await session.get(Swarm, row.swarm_id)
    return swarm.id if swarm is not None and swarm.status == "active" else None


# ---------------------------------------------------------------- changes
async def create_swarm(session: AsyncSession, *, name: str, public_key_hex: str, description: str | None = None,
                       is_home: bool = False, actor: str = "cli") -> Swarm:
    if not NAME_RE.match(name or ""):
        raise ServiceError(422, "bad_name", "swarm name: lowercase letters, digits, '.', '_' or '-', up to 64")
    try:
        public_key = bytes.fromhex(public_key_hex)
    except ValueError:
        public_key = b""
    if len(public_key) != 32:
        raise ServiceError(422, "bad_key", "public key must be 64 hex characters (Ed25519)")
    if await session.scalar(select(Swarm).where((Swarm.name == name) | (Swarm.public_key == public_key))):
        raise ServiceError(409, "swarm_exists", "a swarm with that name or key already exists")
    if is_home and await session.scalar(select(Swarm).where(Swarm.is_home.is_(True), Swarm.status == "active")):
        raise ServiceError(409, "home_exists", "there is already an active home swarm")
    swarm = Swarm(id=swarm_id_for(public_key), name=name, description=description,
                  public_key=public_key, is_home=is_home)
    session.add(swarm)
    _audit(session, actor, "swarm_create", {"swarm": swarm.id, "name": name, "home": is_home})
    await session.flush()
    return swarm


async def _close(session: AsyncSession, row: SwarmMember, member: Member, now: datetime) -> None:
    row.left_at = now
    if row.role == "hub":
        _without_relay(member)
    await session.flush()  # the partial unique indexes see the closed row before a new one opens


async def set_member(session: AsyncSession, member: Member, swarm: Swarm, role: str, *,
                     actor: str = "cli", now: datetime | None = None) -> SwarmMember:
    """Put a member in a swarm as ``member`` or ``observer``; moves it if it was elsewhere."""
    now = _now(now)
    if role not in MEMBER_ROLES:
        raise ServiceError(422, "bad_role", "role must be member or observer (a hub is appointed with the root key)")
    if swarm.status != "active":
        raise ServiceError(409, "swarm_revoked", f"swarm {swarm.name!r} is revoked")
    if member.status not in ("active", "pending"):
        raise ServiceError(409, "not_active", f"member {member.name!r} is {member.status}")
    current = await open_membership(session, member.id)
    if current is not None:
        if current.swarm_id == swarm.id and current.role == role:
            return current
        await _close(session, current, member, now)
    row = SwarmMember(swarm_id=swarm.id, member_id=member.id, role=role, joined_at=now)
    session.add(row)
    _audit(session, actor, "swarm_join",
           {"swarm": swarm.name, "member": member.name, "role": role,
            "moved_from": current.swarm_id if current is not None else None}, member_id=member.id)
    await session.flush()
    return row


async def appoint_hub(session: AsyncSession, swarm: Swarm, appointment: dict, *,
                      actor: str = "cli", now: datetime | None = None) -> SwarmMember:
    """Make a member the swarm's hub, on the strength of a root-key signature."""
    now = _now(now)
    try:
        statement, sig = appointment["statement"], appointment["sig"]
        ok_shape = (statement.get("v") == 1 and statement.get("action") == "appoint_hub"
                    and isinstance(statement.get("hub"), str) and isinstance(statement.get("hub_key"), str)
                    and isinstance(sig, str))
    except (KeyError, TypeError, AttributeError):
        ok_shape = False
    if not ok_shape:
        raise ServiceError(422, "bad_appointment", "not a hub appointment (see scripts/swarm_key.py)")
    if statement.get("swarm") != swarm.id:
        raise ServiceError(422, "bad_appointment", "the appointment is for a different swarm")
    if not verify_signature(swarm.public_key, appointment_message(statement), sig):
        raise ServiceError(422, "bad_appointment", "the signature doesn't match this swarm's root key")
    try:
        signed_at = parse_ts(statement["ts"])
    except Exception:
        raise ServiceError(422, "bad_appointment", "bad ts in the appointment") from None
    if not (now - APPOINTMENT_MAX_AGE <= signed_at <= now + timedelta(minutes=5)):
        raise ServiceError(422, "stale_appointment", "the appointment is older than 30 days or in the future; sign a new one")
    if swarm.status != "active":
        raise ServiceError(409, "swarm_revoked", f"swarm {swarm.name!r} is revoked")

    member = await session.scalar(select(Member).where(Member.name == statement["hub"]))
    if member is None:
        raise ServiceError(404, "no_member", f"no member named {statement['hub']!r}")
    if bytes(member.public_key).hex() != statement["hub_key"].lower():
        raise ServiceError(422, "bad_appointment", "hub_key doesn't match that member's enrolled key")
    if member.status != "active":
        raise ServiceError(409, "not_active", f"member {member.name!r} is {member.status}")

    old_hub = await session.scalar(select(SwarmMember).where(
        SwarmMember.swarm_id == swarm.id, SwarmMember.role == "hub", SwarmMember.left_at.is_(None)))
    if old_hub is not None and old_hub.member_id != member.id:
        await _close(session, old_hub, await session.get(Member, old_hub.member_id), now)
    current = await open_membership(session, member.id)
    if current is not None:
        await _close(session, current, member, now)
    row = SwarmMember(swarm_id=swarm.id, member_id=member.id, role="hub", joined_at=now,
                      appointment=appointment)
    session.add(row)
    if "relay" not in member.permissions:
        member.permissions = [*member.permissions, "relay"]
    _audit(session, actor, "swarm_appoint_hub",
           {"swarm": swarm.name, "hub": member.name, "replaced": str(old_hub.member_id) if old_hub else None},
           member_id=member.id)
    await session.flush()
    return row


async def remove_member(session: AsyncSession, member: Member, *, actor: str = "cli",
                        now: datetime | None = None) -> bool:
    """Make a member a free agent. A hub loses its relay permission."""
    current = await open_membership(session, member.id)
    if current is None:
        return False
    await _close(session, current, member, _now(now))
    _audit(session, actor, "swarm_leave", {"swarm": current.swarm_id, "member": member.name, "role": current.role},
           member_id=member.id)
    return True


async def revoke_swarm(session: AsyncSession, swarm: Swarm, *, actor: str = "cli",
                       now: datetime | None = None) -> int:
    """Revoke a swarm: every member becomes a free agent and the hub stops relaying.

    Records keep the provenance they were stored with.
    """
    now = _now(now)
    rows = list(await session.scalars(select(SwarmMember).where(
        SwarmMember.swarm_id == swarm.id, SwarmMember.left_at.is_(None))))
    for row in rows:
        await _close(session, row, await session.get(Member, row.member_id), now)
    swarm.status, swarm.revoked_at = "revoked", now
    _audit(session, actor, "swarm_revoke", {"swarm": swarm.name, "members_released": len(rows)})
    await session.flush()
    return len(rows)


async def describe(session: AsyncSession) -> list[dict]:
    out = []
    for swarm in await session.scalars(select(Swarm).order_by(Swarm.created_at)):
        rows = await session.execute(
            select(SwarmMember.role, Member.name, SwarmMember.joined_at)
            .join(Member, Member.id == SwarmMember.member_id)
            .where(SwarmMember.swarm_id == swarm.id, SwarmMember.left_at.is_(None))
            .order_by(SwarmMember.role, Member.name))
        out.append({"id": swarm.id, "name": swarm.name, "status": swarm.status, "home": swarm.is_home,
                    "members": [{"name": n, "role": r, "since": j.isoformat()} for r, n, j in rows]})
    return out
