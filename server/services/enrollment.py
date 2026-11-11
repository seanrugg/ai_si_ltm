"""Enrollment lifecycle: pending -> active -> revoked (or pending -> rejected).

Members request enrollment with their public key; nothing is granted until an
admin approves, and the admin decides which permissions (and scope) to grant.
Revoking keeps every record the member wrote and kills its tokens at once.

A swarm sets how members join: ``approval`` (today's default; the member stays
pending until an admin approves), ``invite`` (a valid one-time code makes the
member active at once) or ``open`` (anyone may join and is active at once with
the swarm's ``open_permissions``). Free agents (no swarm) always use approval.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from nacl.signing import VerifyKey
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ltms_contract.record import NAME_RE
from server.models import (
    AuditLog, EnrollmentSwarmRequest, Invite, Member, Swarm, SwarmMember, Token,
)
from server.models.member import MEMBER_KINDS, PERMISSIONS
from server.services import cognition, swarms
from server.services.errors import ServiceError


def _validate_permissions(perms: list[str]) -> list[str]:
    bad = [p for p in perms if p not in PERMISSIONS]
    if bad:
        raise ServiceError(422, "bad_permissions", f"unknown permissions: {', '.join(bad)}")
    # "relay" comes only with a signed hub appointment (server/services/swarms.py),
    # never from an enrollment request or a permissions edit.
    return sorted(set(perms) - {"relay"})


def _validate_scope(scope: dict | None) -> dict | None:
    if scope is None:
        return None
    if not isinstance(scope, dict) or set(scope) - {"types", "agents", "places", "swarms"}:
        raise ServiceError(422, "bad_scope", "scope may only contain types, agents, places, swarms")
    for key, val in scope.items():
        if not isinstance(val, list):
            raise ServiceError(422, "bad_scope", f"scope.{key} must be a list of strings")
        if key == "swarms":
            if not all(v is None or isinstance(v, str) for v in val):
                raise ServiceError(422, "bad_scope", f"scope.{key} must be a list of strings")
        elif not all(isinstance(v, str) for v in val):
            raise ServiceError(422, "bad_scope", f"scope.{key} must be a list of strings")
    return scope


INVITE_PREFIX = "ltms_inv_"

# In-memory refusal counter for auto_deny requests (one uvicorn worker, so a
# module-level counter is fine). Count plus a since-timestamp; exposed on the
# policy GET and aggregated into one hourly audit row by the scheduler.
_ENROLL_REFUSALS = {"count": 0, "since": None}


def _record_refusal(now) -> None:
    if _ENROLL_REFUSALS["count"] == 0:
        _ENROLL_REFUSALS["since"] = now
    _ENROLL_REFUSALS["count"] += 1


def refusal_counter() -> dict:
    """Snapshot of the in-memory refusal counter (count + since)."""
    return {"count": _ENROLL_REFUSALS["count"], "since": _ENROLL_REFUSALS["since"]}


def take_refusals() -> tuple:
    """Read and reset the refusal counter: (count, since)."""
    count = _ENROLL_REFUSALS["count"]
    since = _ENROLL_REFUSALS["since"]
    _ENROLL_REFUSALS["count"] = 0
    _ENROLL_REFUSALS["since"] = None
    return count, since


def _invite_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


async def create_invite(
    session: AsyncSession, swarm: Swarm, *, uses: int, expires_at: datetime | None,
    actor: str = "cli",
) -> tuple[Invite, str]:
    """Create a one-time enrollment code for ``swarm``.

    Returns the row and the plaintext code. Only the code's SHA-256 is stored,
    so the caller must show the plaintext now; it can't be recovered later.
    """
    if swarm.status != "active":
        raise ServiceError(409, "swarm_revoked", f"swarm {swarm.name!r} is revoked")
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise ServiceError(422, "bad_expiry", "expires_at must be in the future")
    code = INVITE_PREFIX + secrets.token_urlsafe(24)
    row = Invite(swarm_id=swarm.id, code_hash=_invite_hash(code), uses_left=uses,
                 expires_at=expires_at)
    session.add(row)
    await session.flush()
    session.add(AuditLog(actor=actor, action="swarm_invite_create",
                         detail={"swarm": swarm.name, "uses": uses,
                                 "expires_at": expires_at.isoformat() if expires_at else None}))
    return row, code


async def _use_invite(session: AsyncSession, swarm: Swarm, code: str, now: datetime) -> None:
    """Spend one use of an invite code, atomically.

    The decrement is a single conditional UPDATE, so two enrollments racing
    for the last use can't both win: the loser updates no row and gets 403.
    """
    row = await session.scalar(select(Invite).where(
        Invite.swarm_id == swarm.id, Invite.code_hash == _invite_hash(code)))
    if row is None:
        raise ServiceError(403, "bad_invite", "unknown invite code")
    if row.expires_at is not None and row.expires_at <= now:
        raise ServiceError(403, "bad_invite", "invite code has expired")
    result = await session.execute(
        update(Invite)
        .where(Invite.id == row.id, Invite.uses_left > 0)
        .values(uses_left=Invite.uses_left - 1)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ServiceError(403, "bad_invite", "invite code has no uses left")


async def request_enrollment(
    session: AsyncSession,
    *,
    name: str,
    kind: str,
    public_key_hex: str,
    permissions: list[str],
    operator: str | None,
    note: str | None,
    remote_ip: str | None,
    max_pending: int,
    swarm: str | None = None,
    invite: str | None = None,
    enrollment_policy: str = "manual",
) -> Member:
    if not NAME_RE.match(name or ""):
        raise ServiceError(422, "bad_name", "name must match ^[a-z0-9][a-z0-9._-]{0,63}$")
    if kind not in MEMBER_KINDS:
        raise ServiceError(422, "bad_kind", f"kind must be one of {', '.join(MEMBER_KINDS)}")
    try:
        pk = bytes.fromhex(public_key_hex)
        VerifyKey(pk)
        if len(pk) != 32:
            raise ValueError
    except (ValueError, TypeError):
        raise ServiceError(422, "bad_public_key", "public_key must be 64 hex chars (Ed25519)") from None
    requested = _validate_permissions(permissions)

    now = datetime.now(timezone.utc)
    swarm_row = None

    # --- Enrollment policy: applied after validation and before the
    # --- existing-member lookup, so a closed instance doesn't reveal which
    # --- names or keys exist.
    auto = False
    if enrollment_policy == "auto_deny":
        if invite is None:
            _record_refusal(now)
            raise ServiceError(403, "enrollment_closed", "enrollment is closed")
        if swarm is None:
            _record_refusal(now)
            raise ServiceError(403, "enrollment_closed", "enrollment is closed")
        swarm_row = await swarms.get_swarm(session, swarm)  # 404 no_swarm propagates
        if swarm_row.status != "active" or swarm_row.enrollment_mode != "invite":
            _record_refusal(now)
            raise ServiceError(403, "enrollment_closed", "enrollment is closed")
        auto = True
    elif enrollment_policy == "auto_approve":
        if swarm is None:
            # Free agent: approve at once with recall only, whatever was asked.
            # Flood guard: at most 20 policy auto-approvals per hour,
            # instance-wide; above that the request degrades to pending.
            since = now - timedelta(hours=1)
            count = await session.scalar(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == "enroll_auto_policy", AuditLog.ts >= since))
            if count < 20:
                existing = await session.scalar(select(Member).where(
                    or_(Member.name == name, Member.public_key == pk)))
                if existing is not None:
                    raise ServiceError(409, "already_exists", "a member with this name or key already exists")
                member = Member(
                    name=name, kind=kind, public_key=pk, operator=operator, note=note,
                    requested_permissions=requested, permissions=["recall"],
                    status="active", request_ip=remote_ip, enrolled_at=now,
                )
                session.add(member)
                await session.flush()
                session.add(AuditLog(member_id=member.id, actor=name, action="enroll_auto_policy",
                                     detail={"kind": kind, "requested": requested, "granted": ["recall"],
                                             "swarm": None, "ip": remote_ip}))
                return member
            # flood exceeded: fall through to the pending path below
        # names a swarm: that swarm's own mode applies (approval stays pending)

    existing = await session.scalar(select(Member).where(or_(Member.name == name, Member.public_key == pk)))
    if existing is not None:
        raise ServiceError(409, "already_exists", "a member with this name or key already exists")

    if swarm_row is None and swarm is not None:
        # Resolve now (manual, auto_approve naming a swarm, or auto_deny with
        # an invite already resolved above).
        swarm_row = await swarms.get_swarm(session, swarm)
        if swarm_row.status != "active":
            raise ServiceError(422, "bad_swarm", "swarm is not active")

    mode = swarm_row.enrollment_mode if swarm_row is not None else "approval"
    if not auto:
        auto = mode in ("invite", "open")

    granted = list(requested)
    if auto:
        if mode == "invite":
            if invite is None:
                raise ServiceError(403, "bad_invite", "this swarm requires an invite code")
            await _use_invite(session, swarm_row, invite, now)
            # An invitee only ever gets contribute and/or recall, whatever it
            # asked for.
            granted = sorted(set(requested) & {"contribute", "recall"})
        else:
            # Open enrollment: rate limit to 20 per swarm per hour.
            since = now - timedelta(hours=1)
            count = await session.scalar(
                select(func.count()).select_from(Member)
                .join(SwarmMember, SwarmMember.member_id == Member.id)
                .where(
                    # Members who joined and then left still count, so
                    # join-and-leave can't get around the limit.
                    SwarmMember.swarm_id == swarm_row.id,
                    Member.note == "open-enrolled",
                    Member.created_at >= since,
                ))
            if count >= 20:
                raise ServiceError(429, "slow_down",
                                   "this swarm has had too many open enrollments this hour; try later")
            granted = list(swarm_row.open_permissions)
            note = "open-enrolled"
    else:
        pending = await session.scalar(select(func.count()).select_from(Member).where(Member.status == "pending"))
        if pending >= max_pending:
            raise ServiceError(429, "too_many_pending", "too many pending enrollment requests; try later")

    member = Member(
        name=name,
        kind=kind,
        public_key=pk,
        operator=operator,
        note=note,
        requested_permissions=requested,
        permissions=granted if auto else [],
        status="active" if auto else "pending",
        request_ip=remote_ip,
        enrolled_at=now if auto else None,
    )
    session.add(member)
    await session.flush()
    if swarm_row is not None and auto:
        await swarms.set_member(session, member, swarm_row, "member", actor=f"enroll:{name}")
    elif swarm_row is not None:
        session.add(EnrollmentSwarmRequest(member_id=member.id, swarm_id=swarm_row.id))
    session.add(AuditLog(member_id=member.id, actor=name,
                         action="enroll_auto" if auto else "enroll_request",
                         detail={"kind": kind, "mode": mode, "granted": granted,
                                 "swarm": swarm_row.name if swarm_row is not None else None,
                                 "ip": remote_ip}))
    return member


async def get_member(session: AsyncSession, member_id: uuid.UUID) -> Member:
    member = await session.get(Member, member_id)
    if member is None:
        raise ServiceError(404, "not_found", "member not found")
    return member


async def approve(session: AsyncSession, member_id, *, admin: str,
                  permissions: list[str] | None = None, scope: dict | None = None,
                  join_swarm: bool = True) -> Member:
    member = await get_member(session, member_id)
    if member.status != "pending":
        raise ServiceError(409, "bad_state", f"member is {member.status}, not pending")
    granted = _validate_permissions(permissions if permissions is not None else member.requested_permissions)
    scope = _validate_scope(scope)
    member.permissions = granted
    member.scope = scope
    member.status = "active"
    member.enrolled_at = datetime.now(timezone.utc)

    swarm_name = None
    joined = False
    req = await session.get(EnrollmentSwarmRequest, member.id)
    if req is not None:
        swarm = await session.get(Swarm, req.swarm_id)
        swarm_name = swarm.name if swarm is not None else None
        if join_swarm and swarm is not None and swarm.status == "active":
            await swarms.set_member(session, member, swarm, "member", actor=f"admin:{admin}")
            joined = True
        await session.delete(req)
    session.add(AuditLog(member_id=member.id, actor=f"admin:{admin}", action="enroll_approve",
                         detail={"permissions": granted, "scope": member.scope,
                                 "swarm": swarm_name, "joined": joined}))
    return member


async def reject(session: AsyncSession, member_id, *, admin: str, reason: str | None = None) -> Member:
    member = await get_member(session, member_id)
    if member.status != "pending":
        raise ServiceError(409, "bad_state", f"member is {member.status}, not pending")
    member.status = "rejected"
    req = await session.get(EnrollmentSwarmRequest, member.id)
    if req is not None:
        await session.delete(req)
    session.add(AuditLog(member_id=member.id, actor=f"admin:{admin}", action="enroll_reject",
                         detail={"reason": reason}))
    return member


async def revoke_tokens(session: AsyncSession, member_id) -> None:
    await session.execute(
        update(Token)
        .where(Token.member_id == member_id, Token.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


async def revoke(session: AsyncSession, member_id, *, admin: str, reason: str | None = None) -> Member:
    member = await get_member(session, member_id)
    if member.status != "active":
        raise ServiceError(409, "bad_state", f"member is {member.status}, not active")
    member.status = "revoked"
    member.revoked_at = datetime.now(timezone.utc)
    await revoke_tokens(session, member.id)
    session.add(AuditLog(member_id=member.id, actor=f"admin:{admin}", action="member_revoke",
                         detail={"reason": reason}))
    return member


async def set_permissions(session: AsyncSession, member_id, *, admin: str,
                          permissions: list[str], scope: dict | None) -> Member:
    member = await get_member(session, member_id)
    if member.status not in ("active", "pending"):
        raise ServiceError(409, "bad_state", f"member is {member.status}")
    before = {"permissions": member.permissions, "scope": member.scope}
    permissions, scope = _validate_permissions(permissions), _validate_scope(scope)
    row = await swarms.open_membership(session, member.id)
    if row is not None and row.role == "hub":
        permissions = sorted([*permissions, "relay"])  # kept while it is the appointed hub
    member.permissions = permissions
    member.scope = scope
    # Outstanding tokens carry the old permission snapshot; force a fresh login.
    await revoke_tokens(session, member.id)
    session.add(AuditLog(member_id=member.id, actor=f"admin:{admin}", action="permissions_change",
                         detail={"before": before, "after": {"permissions": member.permissions, "scope": member.scope}}))
    return member


def member_to_dict(m: Member) -> dict:
    return {
        "id": str(m.id),
        "name": m.name,
        "kind": m.kind,
        "operator": m.operator,
        "note": m.note,
        "public_key": m.public_key.hex(),
        "status": m.status,
        "permissions": m.permissions,
        "requested_permissions": m.requested_permissions,
        "scope": m.scope,
        "last_ack_seq": m.last_ack_seq,
        "last_hash": m.last_hash,
        "memory_generation": m.memory_generation,
        "recall_floor_id": m.recall_floor_id,
        "cognition": cognition.resolve(m),
        "enrolled_at": m.enrolled_at.isoformat() if m.enrolled_at else None,
        "revoked_at": m.revoked_at.isoformat() if m.revoked_at else None,
        "last_seen_at": m.last_seen_at.isoformat() if m.last_seen_at else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


async def enroll_auto_denied(session: AsyncSession) -> dict:
    """Hourly scheduler job: write one aggregated audit row for the auto_denied
    refusals since the last run, then reset the in-memory counter. Only writes
    when the count is > 0; no per-request audit rows are written in the request
    path.
    """
    count, since = take_refusals()
    if count > 0:
        session.add(AuditLog(actor="scheduler", action="enroll_auto_denied",
                             detail={"count": count,
                                     "since": since.isoformat() if since else None}))
    return {"enroll_auto_denied": count}


async def enroll_expire_pending(session: AsyncSession, ttl_days: int) -> dict:
    """Hourly scheduler job: reject pending members older than ``ttl_days`` with
    reason ``expired``, using ``enrollment.reject`` so the audit row matches a
    manual reject.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    result = await session.scalars(
        select(Member).where(Member.status == "pending", Member.created_at < cutoff))
    rejected = 0
    for member in result.all():
        await reject(session, member.id, admin="scheduler", reason="expired")
        rejected += 1
    return {"enroll_expire_pending": rejected}
