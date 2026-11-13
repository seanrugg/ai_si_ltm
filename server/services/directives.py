"""Directives (#303): signed instructions from the fleet to one swarm or all swarms.

The server signs ``ltms-directive-v1\\n`` + JCS({"v":1,"id","scope","text","payload",
"issued_by","issued_at","expires_at"}) with the fleet key. Cancelling adds a signed
cancel statement (``ltms-directive-cancel-v1\\n`` + JCS({"v":1,"id","cancelled_at"}));
the original statement never changes. Status ``expired`` is worked out on read.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ltms_contract import canonicalize
from server.models import AuditLog, Directive, Swarm
from server.services import fleet_key
from server.services.errors import ServiceError

DOMAIN = "ltms-directive-v1\n"
CANCEL_DOMAIN = "ltms-directive-cancel-v1\n"
MAX_TEXT = 2000
MAX_PAYLOAD = 16 * 1024
DEFAULT_DAYS = 7
MAX_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_of(d: Directive, now: datetime | None = None) -> str:
    if d.status == "cancelled":
        return "cancelled"
    exp = d.expires_at if d.expires_at.tzinfo else d.expires_at.replace(tzinfo=timezone.utc)
    return "expired" if exp <= (now or _now()) else "open"


async def _scope(session, scope) -> tuple[dict, str | None]:
    """(signed scope, swarm id or None for all)."""
    if not isinstance(scope, dict):
        raise ServiceError(422, "bad_scope", 'scope is {"swarm": <name or id>} or {"all": true}')
    if scope.get("all") is True and "swarm" not in scope:
        return {"all": True}, None
    ref = scope.get("swarm")
    if not isinstance(ref, str) or not ref:
        raise ServiceError(422, "bad_scope", 'scope is {"swarm": <name or id>} or {"all": true}')
    swarm = await session.scalar(select(Swarm).where((Swarm.id == ref) | (Swarm.name == ref)))
    if swarm is None or swarm.status != "active":
        raise ServiceError(404, "no_swarm", f"no active swarm {ref!r}")
    return {"swarm": swarm.id}, swarm.id


async def issue(session, cfg, body: dict, *, issued_by: str, signer: str = "fleet",
                sign=None) -> dict:
    """Issue and sign a directive. ``sign(domain, bytes) -> hex`` defaults to the fleet key."""
    if not isinstance(body, dict):
        raise ServiceError(422, "bad_directive", "expected an object")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ServiceError(422, "bad_directive", f"text is required, at most {MAX_TEXT} characters")
    payload = body.get("payload")
    if payload is not None:
        if not isinstance(payload, dict):
            raise ServiceError(422, "bad_directive", "payload must be an object")
        if len(canonicalize(payload)) > MAX_PAYLOAD:
            raise ServiceError(413, "too_large", f"payload is at most {MAX_PAYLOAD} bytes")
    days = body.get("expires_in_days", DEFAULT_DAYS)
    if isinstance(days, bool) or not isinstance(days, (int, float)) or not 0 < days <= MAX_DAYS:
        raise ServiceError(422, "bad_directive", f"expires_in_days is between 0 and {MAX_DAYS}")
    scope, swarm_id = await _scope(session, body.get("scope"))
    now = _now().replace(microsecond=0)
    d = Directive(id=uuid.uuid4(), scope_swarm_id=swarm_id, text=text.strip(), payload=payload,
                  issued_by=issued_by, issued_at=now, expires_at=now + timedelta(days=days), status="open",
                  signer=signer)
    stmt = {"v": 1, "id": str(d.id), "scope": scope, "text": d.text, "payload": payload,
            "issued_by": issued_by, "issued_at": _iso(now), "expires_at": _iso(d.expires_at)}
    raw = canonicalize(stmt)
    d.statement = raw.decode("utf-8")
    d.signature = (sign or (lambda dom, b: fleet_key.sign(cfg, dom, b)))(DOMAIN, raw)
    session.add(d)
    session.add(AuditLog(actor=issued_by, action="directive_issue",
                         detail={"directive": str(d.id), "scope": scope, "signer": signer}))
    await session.flush()
    return await to_dict(session, d)


async def cancel(session, cfg, directive_id, *, actor: str, sign=None) -> dict:
    d = await get(session, directive_id)
    if d.status == "cancelled":
        raise ServiceError(409, "already_cancelled", "this directive is already cancelled")
    now = _now().replace(microsecond=0)
    raw = canonicalize({"v": 1, "id": str(d.id), "cancelled_at": _iso(now)})
    d.status, d.cancelled_at = "cancelled", now
    d.cancel_statement = raw.decode("utf-8")
    d.cancel_signature = (sign or (lambda dom, b: fleet_key.sign(cfg, dom, b)))(CANCEL_DOMAIN, raw)
    session.add(AuditLog(actor=actor, action="directive_cancel", detail={"directive": str(d.id)}))
    await session.flush()
    return await to_dict(session, d)


async def get(session, directive_id) -> Directive:
    try:
        key = uuid.UUID(str(directive_id))
    except ValueError:
        raise ServiceError(404, "not_found", "no such directive") from None
    d = await session.get(Directive, key)
    if d is None:
        raise ServiceError(404, "not_found", "no such directive")
    return d


async def to_dict(session, d: Directive) -> dict:
    name = await session.scalar(select(Swarm.name).where(Swarm.id == d.scope_swarm_id)) if d.scope_swarm_id else None
    return {"id": str(d.id), "scope": {"swarm": d.scope_swarm_id, "swarm_name": name} if d.scope_swarm_id else {"all": True},
            "text": d.text, "payload": d.payload, "issued_by": d.issued_by, "issued_at": _iso(d.issued_at),
            "expires_at": _iso(d.expires_at), "status": status_of(d), "signer": d.signer,
            "statement": d.statement, "signature": d.signature,
            "cancel": ({"statement": d.cancel_statement, "signature": d.cancel_signature}
                       if d.cancel_statement else None)}


async def list_all(session, limit: int = 100) -> list[dict]:
    rows = await session.scalars(select(Directive).order_by(Directive.issued_at.desc()).limit(limit))
    return [await to_dict(session, d) for d in rows]
