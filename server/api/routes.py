"""All FastAPI route definitions (plan §"API Surface").

Grouped into three routers by auth scope:

* ``public_router`` — ``/api/auth/enroll`` and ``/api/auth/challenge`` (no auth).
* ``member_router`` — bearer-token endpoints under ``/api``.
* ``admin_router`` — session-cookie endpoints under ``/api/admin``.

Each router is mounted by ``main.py``. Handlers are thin: they parse the request,
call a service, and return JSON. All DB access goes through ``session_scope``.
"""
from __future__ import annotations

import secrets
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.schemas import (
    AdminLogin,
    BatchResponse,
    ChallengeResponse,
    EnrollRequest,
    MemberResponse,
    RecordBatch,
    RecordOutcome,
    RecordIn,
    RecallContextQuery,
    RecallQuery,
    SetPermissions,
    TokenRequest,
    TokenResponse,
)
from server.config import get_config
from server.db import async_session_factory, session_scope
from server.models.adminsession import AdminSession
from server.models.audit import AuditLog
from server.models.adminuser import AdminUser
from server.models.member import Member
from server.models.token import ActiveToken
from server.security.auth import issue_session, verify_password
from server.security.deps import ADMIN_COOKIE, AuthError, current_member, require_admin
from server.security.key import Ed25519PublicKey, verify_nonce
from server.security.token import hash_token, issue_token
from server.services import chain as chain_service  # noqa: F401  (re-exported)
from server.services import enrollment, integrity, recall as recall_service, retention
from server.services.ingest import ingest_batch

config = get_config()

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_uuid(value: str) -> _uuid.UUID:
    """Parse a UUID string, raising 404 on failure."""
    try:
        return _uuid.UUID(value)
    except (ValueError, AttributeError):
        raise AuthError(404, "member not found")


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
public_router = APIRouter(tags=["public"])

# In-memory nonce store for /api/auth/challenge. Production would use a shared
# cache; the nonce is single-use and short-lived (45 s default).
_NONCE_STORE: dict[str, datetime] = {}


@public_router.post("/api/auth/challenge", response_model=ChallengeResponse)
async def get_challenge() -> ChallengeResponse:
    """Hand out a single-use nonce within the replay window."""
    nonce = secrets.token_hex(16)
    _NONCE_STORE[nonce] = datetime.now(timezone.utc) + timedelta(
        seconds=config.auth.max_replay_sec
    )
    return ChallengeResponse(nonce=nonce, expires_at=_NONCE_STORE[nonce])


@public_router.post("/api/auth/enroll", status_code=201)
async def enroll(request: EnrollRequest) -> dict:
    """Submit an enrollment request (stored pending for admin approval)."""
    async with session_scope() as session:
        member = await enrollment.create_enrollment_request(
            session,
            name=request.name,
            kind=request.kind,
            operator=request.operator,
        )
        session.add(
            AuditLog(
                ts=datetime.now(timezone.utc),
                action="enroll_request",
                detail={"name": member.name, "kind": member.kind},
            )
        )
    return {"status": "pending", "member_id": str(member.id)}


# --------------------------------------------------------------------------- #
# Member
# --------------------------------------------------------------------------- #
member_router = APIRouter(tags=["member"])


@member_router.post("/api/auth/token", response_model=TokenResponse)
async def exchange_token(
    request: TokenRequest,
    member: Member = Depends(current_member),
) -> dict:
    """Exchange a signed challenge nonce for a bearer token.

    The member's public key is already stored; the signature over
    ``ltms-auth-v1 : nonce`` proves they hold the secret key. This endpoint is
    reachable without a prior token (current_member accepts the signed nonce as
    proof), but the nonce must be fresh (from /api/auth/challenge).
    """
    nonce = request.nonce
    expires_at = _NONCE_STORE.get(nonce)
    if expires_at is None or expires_at < datetime.now(timezone.utc):
        raise AuthError(400, "nonce expired or unknown")
    del _NONCE_STORE[nonce]  # single use

    raw = issue_token()
    now = datetime.now(timezone.utc)
    token_hash = hash_token(raw)

    async with session_scope() as session:
        # The member's stored public key verifies the nonce signature.
        verifying = Ed25519PublicKey(member.public_key)
        if not verify_nonce(verifying, nonce, request.signature):
            raise AuthError(401, "nonce signature does not verify")

        session.add(
            ActiveToken(
                member_id=member.id,
                token_hash=token_hash,
                permissions=member.permissions or [],
                issued_at=now,
                expires_at=now + timedelta(seconds=config.auth.token_ttl_seconds),
            )
        )
    return {"token": raw.hex(), "expires_at": now + timedelta(seconds=config.auth.token_ttl_seconds)}


@member_router.post("/api/records", response_model=BatchResponse)
async def ingest_records(
    batch: RecordBatch,
    member: Member = Depends(current_member),
) -> dict:
    """Ingest a batch of records for the current member."""
    if len(batch.records) > config.rate_limits.max_batch:
        raise HTTPException(status_code=400, detail="batch exceeds max_batch")

    records = [r.model_dump() for r in batch.records]
    async with session_scope() as session:
        outcomes = await ingest_batch(
            session,
            member,
            records,
            replay_sec=config.auth.max_replay_sec,
            skew_sec=config.auth.clock_skew_sec,
        )
    return BatchResponse(outcomes=outcomes)


@member_router.get("/api/records")
async def recall(
    query: RecallQuery = Depends(),
    member: Member = Depends(current_member),
) -> dict:
    """Recall the current member's own records."""
    async with session_scope() as session:
        rows = await recall_service.recall_records(
            session,
            member,
            type_=query.type,
            agent_=query.agent,
            place_=query.place,
            from_=query.from_,
            to=query.to,
            limit=query.limit,
        )
    return rows


@member_router.get("/api/recall/context")
async def recall_context(
    query: RecallContextQuery = Depends(),
    member: Member = Depends(current_member),
) -> dict:
    """Compose a context pack for a situation."""
    async with session_scope() as session:
        return await recall_service.recall_context(
            session,
            member,
            intent=query.intent,
            target=query.target,
            place=query.place,
        )


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
admin_router = APIRouter(tags=["admin"])


@admin_router.post("/api/auth/admin/login")
async def admin_login(request: AdminLogin) -> dict:
    """Admin login → sets the ltms_admin session cookie."""
    async with session_scope() as session:
        stmt = select(AdminUser).where(AdminUser.username == request.username)
        admin = await session.scalar(stmt)
        if admin is None or not verify_password(request.password, admin.password_hash):
            raise AuthError(401, "invalid admin credentials")

        session_id = issue_session()
        now = datetime.now(timezone.utc)
        session.add(
            AdminSession(
                session_id=session_id,
                username=admin.username,
                issued_at=now,
                expires_at=now + timedelta(seconds=config.auth.admin_session_ttl_seconds),
            )
        )
        admin.last_login_at = now
    response: dict = {"status": "ok"}
    response.set_cookie(
        ADMIN_COOKIE,
        session_id.hex(),
        httponly=True,
        samesite="lax",
        max_age=config.auth.admin_session_ttl_seconds,
    )
    return response


@admin_router.post("/api/auth/admin/logout")
async def admin_logout() -> dict:
    """Clear the admin session cookie."""
    response: dict = {"status": "ok"}
    response.delete_cookie(ADMIN_COOKIE)
    return response


@admin_router.get("/api/admin/members", response_model=list[MemberResponse])
async def list_members(_: str = Depends(require_admin)) -> list[dict]:
    """Active + pending members."""
    async with session_scope() as session:
        stmt = select(Member)
        members = await session.scalars(stmt)
        return [m.model_dump() for m in members]


@admin_router.get("/api/admin/members/pending")
async def pending_members(_: str = Depends(require_admin)) -> list[dict]:
    """Enrollment requests to act on."""
    async with session_scope() as session:
        stmt = select(Member).where(Member.status == "pending")
        members = await session.scalars(stmt)
        return [m.model_dump() for m in members]


@admin_router.post("/api/admin/members/{member_id}/approve")
async def approve_member(
    member_id: str, _: str = Depends(require_admin)
) -> dict:
    """Activate a pending member."""
    async with session_scope() as session:
        member = await enrollment.approve_enrollment(session, _parse_uuid(member_id))
        session.add(
            AuditLog(
                ts=datetime.now(timezone.utc),
                member_id=member.id,
                action="enroll_approve",
                detail={"name": member.name},
            )
        )
    return {"status": "active", "member_id": str(member.id)}


@admin_router.post("/api/admin/members/{member_id}/revoke")
async def revoke_member(
    member_id: str, _: str = Depends(require_admin)
) -> dict:
    """Revoke a member (records kept)."""
    async with session_scope() as session:
        member = await enrollment.revoke_member(session, _parse_uuid(member_id))
        session.add(
            AuditLog(
                ts=datetime.now(timezone.utc),
                member_id=member.id,
                action="enroll_reject",
                detail={"name": member.name, "revoked": True},
            )
        )
    return {"status": "revoked", "member_id": str(member.id)}


@admin_router.patch("/api/admin/members/{member_id}/permissions")
async def set_permissions(
    member_id: str, body: SetPermissions, _: str = Depends(require_admin)
) -> dict:
    """Update a member's permission set."""
    async with session_scope() as session:
        member = await enrollment.set_permissions(
            session, _parse_uuid(member_id), body.permissions
        )
    return {"status": "ok", "permissions": member.permissions}


@admin_router.get("/api/admin/integrity")
async def integrity_view(_: str = Depends(require_admin)) -> list[dict]:
    """Per-member chain status."""
    async with session_scope() as session:
        return await integrity.integrity_summary(session)


@admin_router.get("/api/admin/recall-log")
async def recall_log(_: str = Depends(require_admin)) -> dict:
    """Audit log of recalls and other audited actions."""
    async with session_scope() as session:
        stmt = select(AuditLog).order_by(AuditLog.ts.desc())
        rows = await session.scalars(stmt)
        return [{"ts": r.ts.isoformat(), "action": r.action, "detail": r.detail} for r in rows]
