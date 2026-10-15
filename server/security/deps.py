"""FastAPI dependencies for member bearer-token and admin-session auth.

``current_member`` reads the ``Authorization: Bearer <token>`` header, looks up
the token's SHA-256 hash in ``tokens`` (plan §"Security Design"), and returns the
member row if the token is unrevoked and unexpired. ``require_admin`` reads the
``ltms_admin`` session cookie, checks ``admin_sessions``, and returns the admin
username. Both raise :class:`AuthError` (mapped to a JSON error by
``api/errors.py``).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import async_session_factory
from server.models.adminsession import AdminSession
from server.models.member import Member
from server.models.token import ActiveToken
from server.security.auth import verify_password
from server.security.token import hash_token

ADMIN_COOKIE = "ltms_admin"


class AuthError(Exception):
    """Raised when authentication fails.

    ``status_code`` is the HTTP status the API layer should return.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def current_member(
    authorization: str | None = Header(default=None),
    *,
    request: Request,
) -> Member:
    """Return the member for a valid bearer token, or raise ``AuthError``.

    The bearer token is a 32-byte opaque value; only its SHA-256 hash is looked
    up, so the raw token is never compared or stored.
    """
    if authorization is None or not authorization.lower().startswith("bearer "):
        raise AuthError(401, "missing or malformed Authorization header")
    raw_token = authorization[len("bearer "):].strip()
    if not raw_token:
        raise AuthError(401, "empty bearer token")

    token_hash = hash_token(bytes.fromhex(raw_token))
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        stmt = select(ActiveToken).where(ActiveToken.token_hash == token_hash.encode("utf-8"))
        token = await session.scalar(stmt)
        if token is None or token.revoked_at is not None or token.expires_at < now:
            raise AuthError(401, "invalid or expired token")
        stmt = select(Member).where(Member.id == token.member_id)
        member = await session.scalar(stmt)
        if member is None:
            raise AuthError(401, "member not found")

    return member


async def require_admin(request: Request) -> str:
    """Return the admin username for a valid session cookie, or raise.

    A missing/invalid session on an admin path raises 403 (plan §"Security
    Design"): the admin surface is closed except for the two public auth paths.
    """
    cookie = request.cookies.get(ADMIN_COOKIE)
    if not cookie:
        raise AuthError(403, "admin session cookie required")

    session_id = bytes.fromhex(cookie)
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        stmt = select(AdminSession).where(AdminSession.session_id == session_id)
        session_row = await session.scalar(stmt)
        if session_row is None or session_row.expires_at < now:
            raise AuthError(403, "invalid or expired admin session")
        return session_row.username
