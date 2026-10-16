"""Unauthenticated endpoints: enrollment requests and the login handshake.

These are the only routes reachable without a token or admin cookie. Each is
rate-limited per client IP.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from server.api.deps import client_ip, transaction
from server.api.schemas import ChallengeRequest, EnrollRequest, TokenRequest
from server.services import auth as auth_service
from server.services import enrollment
from server.services.errors import ServiceError

router = APIRouter(prefix="/api/auth", tags=["public"])


def _limit(request: Request, name: str) -> None:
    ip = client_ip(request) or "unknown"
    if not request.app.state.limiters[name].allow(ip):
        raise ServiceError(429, "rate_limited", "too many requests from this address")


@router.post("/enroll", status_code=202)
async def enroll(body: EnrollRequest, request: Request) -> dict:
    """Ask to join. Nothing is granted until an admin approves."""
    _limit(request, "enroll")
    cfg = request.app.state.config
    async with transaction(request) as session:
        member = await enrollment.request_enrollment(
            session,
            name=body.name,
            kind=body.kind,
            public_key_hex=body.public_key,
            permissions=body.permissions,
            operator=body.operator,
            note=body.note,
            remote_ip=client_ip(request),
            max_pending=cfg.max_pending_enrollments,
        )
        return {"id": str(member.id), "name": member.name, "status": member.status}


@router.post("/challenge")
async def challenge(body: ChallengeRequest, request: Request) -> dict:
    """Step 1 of login: get a single-use nonce to sign."""
    _limit(request, "challenge")
    async with transaction(request) as session:
        return await auth_service.issue_challenge(session, body.name, request.app.state.config.challenge_ttl_seconds)


@router.post("/token")
async def token(body: TokenRequest, request: Request) -> dict:
    """Step 2 of login: return Ed25519(b"ltms-auth-v1\\n" || nonce_bytes) for a bearer token."""
    _limit(request, "challenge")
    async with transaction(request) as session:
        return await auth_service.exchange_token(
            session,
            name=body.name,
            nonce=body.nonce,
            sig=body.sig,
            ttl=request.app.state.config.token_ttl_seconds,
            remote_ip=client_ip(request),
        )
