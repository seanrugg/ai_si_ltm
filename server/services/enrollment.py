"""Enrollment: approve / reject / revoke members and set permissions.

The enrollment lifecycle (plan §"Admin") is:

1. A member submits an enrollment request (public, per-IP capped) — stored as a
   ``Member`` row with ``status='pending'``.
2. An admin approves it → ``status='active'``, ``enrolled_at`` set.
3. An admin rejects it → the pending row is removed.
4. An admin revokes an active member → ``status='revoked'`` (records are kept;
   the chain is append-only, only the member is deactivated).
5. An admin updates a member's permission set.

All mutations require admin (enforced by the auth dependency layer); the
functions here perform no authorization themselves — they assume a trusted
caller.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.member import Member


class EnrollmentError(Exception):
    """Raised when an enrollment operation cannot be completed.

    ``code`` is a stable identifier (``"not_found"``, ``"already_active"``,
    ``"bad_kind"``) the API layer maps to a 400/404.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


VALID_KINDS = {"robot", "agent", "assistant", "relay"}


async def create_enrollment_request(
    session: AsyncSession,
    *,
    name: str,
    kind: str,
    operator: str | None = None,
    permissions: list[str] | None = None,
) -> Member:
    """Create a pending enrollment request and return the ``Member`` row.

    ``name`` must be unique; a duplicate raises :class:`EnrollmentError`.
    ``kind`` is validated against the mesh's allowed kinds.
    """
    if kind not in VALID_KINDS:
        raise EnrollmentError(
            "bad_kind",
            f"kind {kind!r} not in {sorted(VALID_KINDS)}",
        )
    stmt = select(Member).where(Member.name == name)
    if await session.scalar(stmt) is not None:
        raise EnrollmentError("duplicate", f"a member named {name!r} already exists")

    member = Member(
        name=name,
        kind=kind,
        operator=operator,
        permissions=permissions or [],
        status="pending",
    )
    session.add(member)
    await session.flush()
    return member


async def approve_enrollment(session: AsyncSession, member_id) -> Member:
    """Activate a pending member."""
    member = await _get_member(session, member_id)
    if member.status == "active":
        raise EnrollmentError("already_active", "member is already active")
    member.status = "active"
    member.enrolled_at = datetime.now(timezone.utc)
    return member


async def reject_enrollment(session: AsyncSession, member_id) -> None:
    """Delete a pending enrollment request."""
    member = await _get_member(session, member_id)
    if member.status != "pending":
        raise EnrollmentError("not_pending", "only pending members can be rejected")
    await session.delete(member)


async def revoke_member(session: AsyncSession, member_id) -> Member:
    """Mark a member revoked (records kept, chain intact)."""
    member = await _get_member(session, member_id)
    member.status = "revoked"
    member.revoked_at = datetime.now(timezone.utc)
    return member


async def set_permissions(session: AsyncSession, member_id, permissions: list[str]) -> Member:
    """Update a member's permission set."""
    member = await _get_member(session, member_id)
    if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
        raise EnrollmentError("bad_permissions", "permissions must be a list of strings")
    member.permissions = permissions
    return member


async def _get_member(session: AsyncSession, member_id) -> Member:
    """Load a member by id, raising :class:`EnrollmentError` if absent."""
    stmt = select(Member).where(Member.id == member_id)
    member = await session.scalar(stmt)
    if member is None:
        raise EnrollmentError("not_found", "member not found")
    return member
