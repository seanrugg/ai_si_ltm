"""LRS endpoints: storage, encrypted credentials and connection tests (issue #59).

Forwarding and xAPI translation are separate issues. This layer only stores
admin-configured endpoints and encrypts their credentials at rest.

The secret is never returned by any API, logged, or written to the audit log.
"""
from __future__ import annotations

import base64
import uuid
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ltms_contract.record import NAME_RE
from server.config import ServerConfig
from server.models import AuditLog, LrsEndpoint
from server.services.enrollment import _validate_scope
from server.services.errors import ServiceError

XAPI_VERSIONS = ("2.0.0", "1.0.3")
AUTH_TYPES = ("basic",)

_UPDATABLE_FIELDS = {"name", "url", "username", "xapi_version", "auth_type", "scope", "enabled"}
PATCH_FIELDS = _UPDATABLE_FIELDS | {"secret"}


def _fernet(cfg: ServerConfig) -> Fernet:
    if not cfg.secret_key:
        raise ServiceError(422, "no_secret_key", "set LTMS_SECRET_KEY to store LRS credentials")
    try:
        return Fernet(cfg.secret_key.encode("utf-8"))
    except (ValueError, TypeError):
        # Don't echo the key: just say it's malformed.
        raise ServiceError(422, "bad_secret_key", "LTMS_SECRET_KEY is not a valid Fernet key") from None


def encrypt_secret(cfg: ServerConfig, plaintext: str) -> bytes:
    return _fernet(cfg).encrypt(plaintext.encode("utf-8"))


def decrypt_secret(cfg: ServerConfig, endpoint: LrsEndpoint) -> str | None:
    """Decrypt an endpoint's stored secret, or None when there is none."""
    if not endpoint.secret_enc:
        return None
    return _fernet(cfg).decrypt(endpoint.secret_enc).decode("utf-8")


def endpoint_to_dict(endpoint: LrsEndpoint) -> dict:
    """Every field except ``secret_enc``, plus ``has_secret``."""
    return {
        "id": str(endpoint.id),
        "name": endpoint.name,
        "url": endpoint.url,
        "xapi_version": endpoint.xapi_version,
        "auth_type": endpoint.auth_type,
        "username": endpoint.username,
        "scope": endpoint.scope,
        "enabled": endpoint.enabled,
        "cursor": endpoint.cursor,
        "last_success_at": endpoint.last_success_at.isoformat() if endpoint.last_success_at else None,
        "last_error_at": endpoint.last_error_at.isoformat() if endpoint.last_error_at else None,
        "last_error": endpoint.last_error,
        "created_at": endpoint.created_at.isoformat() if endpoint.created_at else None,
        "has_secret": bool(endpoint.secret_enc),
    }


def _validate_name(name: str | None) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ServiceError(422, "bad_name", "name must match ^[a-z0-9][a-z0-9._-]{0,63}$")
    return name


def _validate_url(url: str | None) -> str:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ServiceError(422, "bad_lrs", "url must start with http:// or https://")
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ServiceError(422, "bad_lrs", "url must not contain user:pass@ credentials")
    return url.rstrip("/")


def _validate_xapi_version(version: str | None) -> str:
    if version not in XAPI_VERSIONS:
        raise ServiceError(422, "bad_lrs", f"xapi_version must be one of {', '.join(XAPI_VERSIONS)}")
    return version


def _validate_auth_type(auth_type: str | None) -> str:
    if auth_type not in AUTH_TYPES:
        raise ServiceError(422, "bad_lrs", f"auth_type must be one of {', '.join(AUTH_TYPES)}")
    return auth_type


async def get_endpoint(session: AsyncSession, endpoint_id: uuid.UUID | str) -> LrsEndpoint:
    if isinstance(endpoint_id, str):
        try:
            endpoint_id = uuid.UUID(endpoint_id)
        except ValueError as exc:
            raise ServiceError(404, "not_found", "lrs endpoint not found") from exc
    endpoint = await session.get(LrsEndpoint, endpoint_id)
    if endpoint is None:
        raise ServiceError(404, "not_found", "lrs endpoint not found")
    return endpoint


async def list_endpoints(session: AsyncSession) -> list[LrsEndpoint]:
    result = await session.execute(select(LrsEndpoint).order_by(LrsEndpoint.name))
    return list(result.scalars())


async def create_endpoint(
    session: AsyncSession,
    cfg: ServerConfig,
    *,
    name: str,
    url: str,
    username: str = "",
    secret: str | None = None,
    xapi_version: str = "2.0.0",
    scope: dict | None = None,
    actor: str,
) -> LrsEndpoint:
    name = _validate_name(name)
    url = _validate_url(url)
    xapi_version = _validate_xapi_version(xapi_version)
    scope = _validate_scope(scope)
    username = (username or "")

    secret_enc = None
    if secret:
        secret_enc = encrypt_secret(cfg, secret)

    endpoint = LrsEndpoint(
        name=name,
        url=url,
        xapi_version=xapi_version,
        auth_type="basic",
        username=username,
        secret_enc=secret_enc,
        scope=scope,
    )
    session.add(endpoint)
    await session.flush()
    fields = ["name", "url", "xapi_version", "auth_type", "username", "scope"]
    if secret is not None:
        fields.append("secret")
    session.add(AuditLog(actor=actor, action="lrs_create",
                         detail={"endpoint_id": str(endpoint.id), "fields": fields}))
    return endpoint


async def update_endpoint(
    session: AsyncSession,
    cfg: ServerConfig,
    endpoint_id: uuid.UUID | str,
    *,
    actor: str,
    **fields,
) -> LrsEndpoint:
    endpoint = await get_endpoint(session, endpoint_id)
    changed: list[str] = []
    for field, value in fields.items():
        if field == "secret":
            if value is None:
                continue  # unchanged
            if value == "":
                endpoint.secret_enc = None
            else:
                endpoint.secret_enc = encrypt_secret(cfg, value)
            changed.append("secret")
            continue
        if field not in _UPDATABLE_FIELDS:
            continue
        if field == "name":
            value = _validate_name(value)
        elif field == "url":
            value = _validate_url(value)
        elif field == "xapi_version":
            value = _validate_xapi_version(value)
        elif field == "auth_type":
            value = _validate_auth_type(value)
        elif field == "scope":
            value = _validate_scope(value)
        elif field == "enabled":
            if not isinstance(value, bool):
                raise ServiceError(422, "bad_lrs", "enabled must be true or false")
        elif field == "username":
            if not isinstance(value, str):
                raise ServiceError(422, "bad_lrs", "username must be a string")
        setattr(endpoint, field, value)
        changed.append(field)
    session.add(AuditLog(actor=actor, action="lrs_update",
                         detail={"endpoint_id": str(endpoint.id), "changed": changed}))
    return endpoint


async def delete_endpoint(session: AsyncSession, endpoint_id: uuid.UUID | str, *, actor: str) -> None:
    endpoint = await get_endpoint(session, endpoint_id)
    await session.delete(endpoint)
    session.add(AuditLog(actor=actor, action="lrs_delete",
                         detail={"endpoint_id": str(endpoint.id), "name": endpoint.name}))


async def test_connection(cfg: ServerConfig, endpoint: LrsEndpoint, http: httpx.AsyncClient) -> dict:
    """GET the LRS ``/about`` endpoint with Basic auth; never raises."""
    result: dict = {"ok": False, "status": None, "versions": None, "error": None}
    secret = decrypt_secret(cfg, endpoint)
    token = base64.b64encode(f"{endpoint.username}:{secret or ''}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "X-Experience-API-Version": endpoint.xapi_version,
    }
    try:
        resp = await http.get(f"{endpoint.url}/about", headers=headers)
    except httpx.HTTPError as exc:
        result["error"] = str(exc)
        return result
    result["status"] = resp.status_code
    if 200 <= resp.status_code < 300:
        result["ok"] = True
        try:
            data = resp.json()
        except (ValueError, TypeError):
            data = {}
        versions = data.get("version") if isinstance(data, dict) else None
        if isinstance(versions, list):
            result["versions"] = versions
    else:
        result["error"] = f"HTTP {resp.status_code}"
    return result
