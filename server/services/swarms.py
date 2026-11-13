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
from server.models import (
    AuditLog, DeclaredLocation, LrsEndpoint, McpToken, Member, RuntimeSetting, Swarm, SwarmMember,
)
from server.models.member import PERMISSIONS
from server.models.swarm import ENROLLMENT_MODES
from server.services.errors import ServiceError

SWARM_PREFIX = b"ltms-swarm-v1\n"
APPOINTMENT_MAX_AGE = timedelta(days=30)
MEMBER_ROLES = ("member", "observer")  # "hub" only through a signed appointment
# Open enrollment may grant only ordinary member permissions: "relay" needs a
# signed hub appointment and "policy" is an admin power.
_OPEN_PERMISSIONS = {"contribute", "recall", "recall:scoped"}


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
    existing = await session.scalar(select(Swarm).where((Swarm.name == name) | (Swarm.public_key == public_key)))
    if existing is not None:
        if existing.status == "removed" and existing.public_key == public_key:
            raise ServiceError(409, "swarm_exists", "a removed swarm used this key; generate a new one")
        raise ServiceError(409, "swarm_exists", "a swarm with that name or key already exists")
    if is_home and await session.scalar(select(Swarm).where(Swarm.is_home.is_(True), Swarm.status == "active")):
        raise ServiceError(409, "home_exists", "there is already an active home swarm")
    swarm = Swarm(id=swarm_id_for(public_key), name=name, description=description,
                  public_key=public_key, is_home=is_home)
    session.add(swarm)
    _audit(session, actor, "swarm_create", {"swarm": swarm.id, "name": name, "home": is_home})
    await session.flush()
    return swarm


def _renamed_scope(scope, old: str, new: str):
    """A copy of a stored scope with ``old`` replaced by ``new`` in its swarms list, or None if unchanged."""
    if not isinstance(scope, dict) or not isinstance(scope.get("swarms"), list) or old not in scope["swarms"]:
        return None
    return {**scope, "swarms": [new if s == old else s for s in scope["swarms"]]}


async def rename_swarm(session: AsyncSession, swarm: Swarm, new_name: str, *, actor: str = "cli") -> Swarm:
    """Rename a swarm (#231). Its ID, keys, members and records are unchanged.

    Hub appointments are signed over the swarm ID, so they stay valid. Stored
    scopes that list the swarm by name (member recall scopes, MCP tokens, the
    MCP open scope, LRS endpoints) and its declared location follow the rename.
    MQTT topics use the name, so hubs, members and the broker ACL need updating
    by the operator.
    """
    if swarm.status == "removed":
        raise ServiceError(404, "no_swarm", "that swarm was removed")
    if not NAME_RE.match(new_name or ""):
        raise ServiceError(422, "bad_name", "swarm name: lowercase letters, digits, '.', '_' or '-', up to 64")
    if new_name == swarm.name:
        return swarm
    if await session.scalar(select(Swarm).where(Swarm.name == new_name)):
        raise ServiceError(409, "name_taken", f"a swarm named {new_name!r} already exists")
    old = swarm.name
    await _move_name(session, swarm, new_name)
    _audit(session, actor, "swarm_rename", {"swarm": swarm.id, "old": old, "new": new_name})
    await session.flush()
    return swarm


async def _move_name(session: AsyncSession, swarm: Swarm, new_name: str) -> None:
    """Give the swarm a new name; stored scopes and its declared location follow."""
    old = swarm.name
    swarm.name = new_name
    for model in (Member, McpToken, LrsEndpoint):
        for row in await session.scalars(select(model).where(model.scope.is_not(None))):
            changed = _renamed_scope(row.scope, old, new_name)
            if changed is not None:
                row.scope = changed
    mcp = await session.get(RuntimeSetting, "mcp_access")
    if mcp is not None and isinstance(mcp.value, dict):
        changed = _renamed_scope(mcp.value.get("open_scope"), old, new_name)
        if changed is not None:
            mcp.value = {**mcp.value, "open_scope": changed}
    loc = await session.scalar(select(DeclaredLocation).where(
        DeclaredLocation.subject_type == "swarm", DeclaredLocation.subject_name == old))
    if loc is not None:
        loc.subject_name = new_name
    await session.flush()


async def release_members(session: AsyncSession, swarm: Swarm, now: datetime) -> list[Member]:
    """Close every open membership of the swarm (the hub loses relay). Returns the members."""
    rows = list(await session.scalars(select(SwarmMember).where(
        SwarmMember.swarm_id == swarm.id, SwarmMember.left_at.is_(None))))
    released = []
    for row in rows:
        member = await session.get(Member, row.member_id)
        await _close(session, row, member, now)
        released.append(member)
    return released


def removed_name(swarm: Swarm) -> str:
    """Where a removed swarm's name goes, so the name can be reused: ``<name>.removed.<id[:8]>``."""
    suffix = f".removed.{swarm.id[:8]}"
    return swarm.name[:64 - len(suffix)] + suffix


def _validate_open_permissions(perms: list[str]) -> list[str]:
    """Validate ``open_permissions``: known permissions only, never relay or
    an admin permission (policy)."""
    if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
        raise ServiceError(422, "bad_permissions", "open_permissions must be a list of strings")
    bad = [p for p in perms if p not in PERMISSIONS]
    if bad:
        raise ServiceError(422, "bad_permissions", f"unknown permissions: {', '.join(bad)}")
    barred = [p for p in perms if p not in _OPEN_PERMISSIONS]
    if barred:
        raise ServiceError(422, "bad_permissions",
                           f"open enrollment cannot grant: {', '.join(barred)}")
    return sorted(set(perms))


async def set_enrollment_mode(
    session: AsyncSession, swarm: Swarm, mode: str,
    open_permissions: list[str] | None, *,
    actor: str = "cli",
) -> Swarm:
    """Set how members join a swarm and what an open-enrolled member gets."""
    if mode not in ENROLLMENT_MODES:
        raise ServiceError(422, "bad_mode", "mode must be approval, invite or open")
    if swarm.status != "active":
        raise ServiceError(409, "swarm_revoked", f"swarm {swarm.name!r} is revoked")
    swarm.enrollment_mode = mode
    # Omitting open_permissions keeps the swarm's current value, so switching
    # modes back and forth never silently resets a custom setting.
    if open_permissions is not None:
        swarm.open_permissions = _validate_open_permissions(open_permissions)
    _audit(session, actor, "swarm_mode",
           {"swarm": swarm.name, "mode": mode,
            "open_permissions": swarm.open_permissions})
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


def _leader_types() -> list[str]:
    from ltms_contract import RECORD_TYPES

    return [t for t in RECORD_TYPES if t != "system"]


def _grant_leader_recall(member: Member, swarm_name: str) -> dict:
    """Scoped recall of the swarm's records for the leader (#308). Returns what was added,
    so removing the leader takes away exactly that and nothing else."""
    perms, scope = list(member.permissions or []), dict(member.scope or {})
    if "recall" in perms:
        return {}  # already sees everything
    granted: dict = {}
    if "recall:scoped" not in perms:
        perms.append("recall:scoped")
        granted["permission"] = True
        scope = {}
    if scope.get("swarms") is None and not granted.get("permission"):
        return granted  # an existing scope over every swarm: widening it to more types would over-grant
    swarms_ = list(scope.get("swarms") or [])
    if swarm_name not in swarms_:
        swarms_.append(swarm_name)
        granted["swarm"] = swarm_name
    types = list(scope.get("types") or [])
    added_types = [t for t in _leader_types() if t not in types]
    granted["types"] = added_types
    scope.update(swarms=swarms_, types=types + added_types)
    member.permissions, member.scope = perms, scope
    return granted


def _revoke_leader_recall(member: Member, granted: dict | None) -> None:
    if not granted:
        return
    perms, scope = list(member.permissions or []), dict(member.scope or {})
    if granted.get("permission"):
        perms = [p for p in perms if p != "recall:scoped"]
        member.permissions, member.scope = perms, None
        return
    if granted.get("swarm"):
        scope["swarms"] = [x for x in scope.get("swarms") or [] if x != granted["swarm"]]
    if granted.get("types"):
        scope["types"] = [t for t in scope.get("types") or [] if t not in granted["types"]]
    member.scope = scope


async def appoint_leader(session: AsyncSession, swarm: Swarm, appointment: dict, *,
                         actor: str = "cli", now: datetime | None = None) -> Member:
    """Make an active member the swarm's leader, on the strength of a root-key signature (#308)."""
    now = _now(now)
    try:
        statement, sig = appointment["statement"], appointment["sig"]
        ok_shape = (statement.get("v") == 1 and statement.get("action") == "appoint_leader"
                    and isinstance(statement.get("leader"), str) and isinstance(statement.get("leader_key"), str)
                    and isinstance(sig, str))
    except (KeyError, TypeError, AttributeError):
        ok_shape = False
    if not ok_shape:
        raise ServiceError(422, "bad_appointment", "not a leader appointment (see scripts/swarm_key.py)")
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
    member = await session.scalar(select(Member).where(Member.name == statement["leader"]))
    if member is None:
        raise ServiceError(404, "no_member", f"no member named {statement['leader']!r}")
    if bytes(member.public_key).hex() != statement["leader_key"].lower():
        raise ServiceError(422, "bad_appointment", "leader_key doesn't match that member's enrolled key")
    if member.status != "active":
        raise ServiceError(409, "not_active", f"member {member.name!r} is {member.status}")
    hub = await session.scalar(select(SwarmMember).where(
        SwarmMember.swarm_id == swarm.id, SwarmMember.role == "hub", SwarmMember.left_at.is_(None),
        SwarmMember.member_id == member.id))
    if hub is not None:
        raise ServiceError(409, "is_hub", f"{member.name} is this swarm's hub; a member can't be both")

    old_id = swarm.leader_member_id
    if old_id is not None and old_id != member.id:
        old = await session.get(Member, old_id)
        if old is not None:
            _revoke_leader_recall(old, (swarm.leader_appointment or {}).get("granted"))
        from server.services import directives

        await directives.inherit_from(session, swarm, old.name if old else None)
    granted = (swarm.leader_appointment or {}).get("granted") if old_id == member.id else _grant_leader_recall(member, swarm.name)
    swarm.leader_member_id = member.id
    swarm.leader_appointment = {"statement": statement, "sig": sig, "granted": granted}
    _audit(session, actor, "swarm_appoint_leader",
           {"swarm": swarm.name, "leader": member.name, "replaced": str(old_id) if old_id and old_id != member.id else None},
           member_id=member.id)
    await session.flush()
    return member


async def remove_leader(session: AsyncSession, swarm: Swarm, *, actor: str = "cli") -> bool:
    if swarm.leader_member_id is None:
        return False
    old = await session.get(Member, swarm.leader_member_id)
    if old is not None:
        _revoke_leader_recall(old, (swarm.leader_appointment or {}).get("granted"))
    _audit(session, actor, "swarm_remove_leader", {"swarm": swarm.name, "leader": old.name if old else None},
           member_id=swarm.leader_member_id)
    from server.services import directives

    await directives.inherit_from(session, swarm, old.name if old else None)
    swarm.leader_member_id, swarm.leader_appointment = None, None
    await session.flush()
    return True


async def leader_of(session: AsyncSession, swarm: Swarm) -> dict | None:
    if swarm.leader_member_id is None:
        return None
    m = await session.get(Member, swarm.leader_member_id)
    return {"name": m.name, "public_key": bytes(m.public_key).hex()} if m is not None else None


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
    rows = await release_members(session, swarm, now)
    swarm.status, swarm.revoked_at = "revoked", now
    _audit(session, actor, "swarm_revoke", {"swarm": swarm.name, "members_released": len(rows)})
    await session.flush()
    return len(rows)


async def describe(session: AsyncSession) -> list[dict]:
    out = []
    for swarm in await session.scalars(select(Swarm).where(Swarm.status != "removed").order_by(Swarm.created_at)):
        rows = await session.execute(
            select(SwarmMember.role, Member.name, SwarmMember.joined_at)
            .join(Member, Member.id == SwarmMember.member_id)
            .where(SwarmMember.swarm_id == swarm.id, SwarmMember.left_at.is_(None))
            .order_by(SwarmMember.role, Member.name))
        out.append({"id": swarm.id, "name": swarm.name, "status": swarm.status, "home": swarm.is_home,
                    "members": [{"name": n, "role": r, "since": j.isoformat()} for r, n, j in rows]})
    return out
