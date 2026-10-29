"""Enrollment lifecycle: pending -> active -> revoked (or pending -> rejected).

Members request enrollment with their public key; nothing is granted until an
admin approves, and the admin decides which permissions (and scope) to grant.
Revoking keeps every record the member wrote and kills its tokens at once.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from nacl.signing import VerifyKey
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ltms_contract.record import NAME_RE
from server.models import AuditLog, Member, Token
from server.models.member import MEMBER_KINDS, PERMISSIONS
from server.services import swarms
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
    if not isinstance(scope, dict) or set(scope) - {"types", "agents", "places"}:
        raise ServiceError(422, "bad_scope", "scope may only contain types, agents, places")
    for key, val in scope.items():
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            raise ServiceError(422, "bad_scope", f"scope.{key} must be a list of strings")
    return scope


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

    existing = await session.scalar(select(Member).where(or_(Member.name == name, Member.public_key == pk)))
    if existing is not None:
        raise ServiceError(409, "already_exists", "a member with this name or key already exists")
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
        permissions=[],
        status="pending",
        request_ip=remote_ip,
    )
    session.add(member)
    await session.flush()
    session.add(AuditLog(member_id=member.id, actor=name, action="enroll_request",
                         detail={"kind": kind, "requested": requested, "ip": remote_ip}))
    return member


async def get_member(session: AsyncSession, member_id: uuid.UUID) -> Member:
    member = await session.get(Member, member_id)
    if member is None:
        raise ServiceError(404, "not_found", "member not found")
    return member


async def approve(session: AsyncSession, member_id, *, admin: str,
                  permissions: list[str] | None = None, scope: dict | None = None) -> Member:
    member = await get_member(session, member_id)
    if member.status != "pending":
        raise ServiceError(409, "bad_state", f"member is {member.status}, not pending")
    granted = _validate_permissions(permissions if permissions is not None else member.requested_permissions)
    scope = _validate_scope(scope)
    member.permissions = granted
    member.scope = scope
    member.status = "active"
    member.enrolled_at = datetime.now(timezone.utc)
    session.add(AuditLog(member_id=member.id, actor=f"admin:{admin}", action="enroll_approve",
                         detail={"permissions": granted, "scope": member.scope}))
    return member


async def reject(session: AsyncSession, member_id, *, admin: str, reason: str | None = None) -> Member:
    member = await get_member(session, member_id)
    if member.status != "pending":
        raise ServiceError(409, "bad_state", f"member is {member.status}, not pending")
    member.status = "rejected"
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
        "enrolled_at": m.enrolled_at.isoformat() if m.enrolled_at else None,
        "revoked_at": m.revoked_at.isoformat() if m.revoked_at else None,
        "last_seen_at": m.last_seen_at.isoformat() if m.last_seen_at else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
