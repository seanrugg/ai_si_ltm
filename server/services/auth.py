"""Member challenge/token login and admin sessions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ltms_contract import auth_signing_message, verify_signature
from ltms_contract.record import HEX128_RE
from server.models import AdminSession, AdminUser, AuditLog, AuthChallenge, Member, Token
from server.security.passwords import hash_password, verify_password
from server.security.tokens import hash_token, new_nonce_hex, new_token
from server.services.errors import ServiceError


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #
async def issue_challenge(session: AsyncSession, name: str, ttl: int) -> dict:
    member = await session.scalar(select(Member).where(Member.name == name))
    if member is None or member.status != "active":
        # Same answer for unknown and inactive, so this can't enumerate members.
        raise ServiceError(403, "not_active", "no active member with that name")
    nonce = new_nonce_hex()
    expires = _now() + timedelta(seconds=ttl)
    session.add(AuthChallenge(nonce=nonce, member_id=member.id, expires_at=expires))
    return {"nonce": nonce, "expires_at": expires.isoformat()}


async def exchange_token(
    session: AsyncSession, *, name: str, nonce: str, sig: str, ttl: int, remote_ip: str | None
) -> dict:
    challenge = await session.get(AuthChallenge, nonce) if isinstance(nonce, str) and len(nonce) == 64 else None
    member = await session.scalar(select(Member).where(Member.name == name))

    def deny(reason: str) -> ServiceError:
        session.add(AuditLog(member_id=member.id if member else None, actor=name, action="token_denied",
                             detail={"reason": reason, "ip": remote_ip}))
        return ServiceError(401, "auth_failed", "challenge signature not accepted")

    if challenge is None or member is None or challenge.member_id != member.id:
        raise deny("unknown_challenge")
    # Single use, whatever happens next.
    await session.execute(delete(AuthChallenge).where(AuthChallenge.nonce == nonce))
    if challenge.expires_at < _now():
        raise deny("expired_challenge")
    if member.status != "active":
        raise deny("not_active")
    if not isinstance(sig, str) or not HEX128_RE.match(sig) or not verify_signature(
        member.public_key, auth_signing_message(nonce), sig
    ):
        raise deny("bad_signature")

    raw = new_token()
    expires = _now() + timedelta(seconds=ttl)
    session.add(Token(member_id=member.id, token_hash=hash_token(raw), permissions=list(member.permissions),
                      remote_ip=remote_ip, expires_at=expires))
    member.last_seen_at = _now()
    session.add(AuditLog(member_id=member.id, actor=member.name, action="token_issued", detail={"ip": remote_ip}))
    return {
        "token": raw,
        "token_type": "bearer",
        "expires_at": expires.isoformat(),
        "permissions": member.permissions,
        "last_ack_seq": member.last_ack_seq,
        "last_hash": member.last_hash,
    }


async def member_from_token(session: AsyncSession, raw: str) -> tuple[Member, list[str]]:
    """Return the member and its effective permissions, or raise 401/403."""
    token = await session.scalar(select(Token).where(Token.token_hash == hash_token(raw)))
    if token is None or token.revoked_at is not None or token.expires_at < _now():
        raise ServiceError(401, "invalid_token", "token is missing, expired or revoked")
    member = await session.get(Member, token.member_id)
    if member is None or member.status != "active":
        raise ServiceError(403, "not_active", "member is not active")
    effective = [p for p in token.permissions if p in member.permissions]
    member.last_seen_at = _now()
    return member, effective


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
async def ensure_admin(session: AsyncSession, username: str, password: str) -> None:
    """Create the admin account on first start. An existing account is left alone
    (change the password with `python -m server.manage set-admin-password`)."""
    if not password:
        return
    existing = await session.scalar(select(AdminUser).where(AdminUser.username == username))
    if existing is None:
        session.add(AdminUser(username=username, password_hash=hash_password(password)))


async def admin_login(
    session: AsyncSession, *, username: str, password: str, ttl: int, remote_ip: str | None
) -> tuple[str, datetime]:
    admin = await session.scalar(select(AdminUser).where(AdminUser.username == username))
    if not verify_password(password, admin.password_hash if admin else None):
        session.add(AuditLog(actor=f"admin:{username}", action="admin_login_failed", detail={"ip": remote_ip}))
        raise ServiceError(401, "auth_failed", "invalid username or password")
    raw = new_token()
    expires = _now() + timedelta(seconds=ttl)
    session.add(AdminSession(session_hash=hash_token(raw), admin_id=admin.id, remote_ip=remote_ip, expires_at=expires))
    admin.last_login_at = _now()
    session.add(AuditLog(actor=f"admin:{username}", action="admin_login", detail={"ip": remote_ip}))
    return raw, expires


async def admin_from_session(session: AsyncSession, raw: str | None) -> AdminUser:
    if not raw:
        raise ServiceError(401, "not_logged_in", "admin login required")
    row = await session.get(AdminSession, hash_token(raw))
    if row is None or row.revoked_at is not None or row.expires_at < _now():
        raise ServiceError(401, "not_logged_in", "admin session expired")
    admin = await session.get(AdminUser, row.admin_id)
    if admin is None:
        raise ServiceError(401, "not_logged_in", "admin account missing")
    return admin


async def admin_logout(session: AsyncSession, raw: str | None) -> None:
    if raw:
        row = await session.get(AdminSession, hash_token(raw))
        if row is not None and row.revoked_at is None:
            row.revoked_at = _now()
