"""MCP read access: named, scoped, optionally time-limited read tokens.

Issue #106 (Remote MCP 1/3): the settings, the token store and the scope
resolution. The MCP endpoint itself is issue #106's successor (MCP 2/3).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import ServerConfig
from server.models import AuditLog, McpToken
from server.services import enrollment
from server.services.errors import ServiceError
from server.services.recall import Scope, effective_scope


def new_token() -> str:
    """A fresh plaintext read token, e.g. ``ltms_<urlsafe>``."""
    return "ltms_" + secrets.token_urlsafe(32)


def _token_hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _validate_mcp_scope(scope: dict | None) -> dict:
    """Validate a token scope: the enrollment keys plus an optional ``payloads``
    bool. Returns a normalized dict (never None)."""
    if scope is None:
        scope = {}
    if not isinstance(scope, dict) or set(scope) - {"types", "agents", "places", "swarms", "payloads"}:
        raise ServiceError(422, "bad_scope", "scope may only contain types, agents, places, swarms, payloads")
    if "payloads" in scope and not isinstance(scope["payloads"], bool):
        raise ServiceError(422, "bad_scope", "scope.payloads must be a boolean")
    # The standard keys are validated the same way enrollment validates them.
    standard = {k: v for k, v in scope.items() if k != "payloads"}
    enrollment._validate_scope(standard)
    return scope


async def create(
    session: AsyncSession,
    *,
    name: str,
    scope: dict | None,
    expires_at: datetime | None,
    actor: str,
) -> tuple[McpToken, str]:
    """Create a token, store only its hash, return (row, plaintext)."""
    scope = _validate_mcp_scope(scope)
    if not name or len(name) > 64:
        raise ServiceError(422, "bad_name", "name must be 1-64 characters")
    if await session.scalar(select(McpToken.id).where(McpToken.name == name)) is not None:
        raise ServiceError(409, "name_taken", "a token with that name already exists")
    plaintext = new_token()
    row = McpToken(
        name=name,
        token_hash=_token_hash(plaintext),
        scope=scope,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    session.add(
        AuditLog(actor=actor, action="mcp_token_create",
                 detail={"name": name, "scope": scope})
    )
    return row, plaintext


async def revoke(session: AsyncSession, token_id, *, actor: str) -> None:
    """Revoke a token by setting ``revoked_at``."""
    row = await session.get(McpToken, token_id)
    if row is None:
        raise ServiceError(404, "not_found", "token not found")
    row.revoked_at = datetime.now(timezone.utc)
    session.add(
        AuditLog(actor=actor, action="mcp_token_revoke",
                 detail={"name": row.name})
    )


async def resolve(
    session: AsyncSession, cfg: ServerConfig, presented: str | None
) -> Scope | None:
    """The read scope for an incoming MCP request, or ``None`` = denied.

    Mode ``off`` always denies. A presented token (in any mode) that matches a
    non-revoked, unexpired row grants that token's scope; an invalid token
    denies and never falls back to open. With no token and mode ``open``, the
    open scope applies (payloads always off).
    """
    if cfg.mcp_read_mode == "off":
        return None

    if presented is not None:
        presented = presented.strip()
        if not presented:
            return None
        row = await session.scalar(
            select(McpToken).where(McpToken.token_hash == _token_hash(presented))
        )
        if row is None or row.revoked_at is not None:
            return None
        if row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc):
            return None
        row.last_used_at = datetime.now(timezone.utc)
        return _scope_from_dict(row.scope)

    if cfg.mcp_read_mode == "open":
        # No token: the open scope. Payloads are always off for open access.
        return effective_scope(["recall:scoped"], cfg.mcp_open_scope)

    return None


def _scope_from_dict(scope: dict) -> Scope:
    return Scope(
        types=list(scope.get("types") or []),
        agents=scope.get("agents"),
        places=scope.get("places"),
        swarms=scope.get("swarms"),
        payloads=bool(scope.get("payloads") is True),
    )


def include_payloads(scope_dict: dict | None) -> bool:
    """True only for a token scope with ``payloads: true``."""
    return bool(scope_dict) and scope_dict.get("payloads") is True
