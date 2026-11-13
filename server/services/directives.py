"""Directives (#303): signed instructions from the fleet to one swarm or all swarms.

The server signs ``ltms-directive-v1\\n`` + JCS({"v":1,"id","scope","text","payload",
"issued_by","issued_at","expires_at"}) with the fleet key. Cancelling adds a signed
cancel statement (``ltms-directive-cancel-v1\\n`` + JCS({"v":1,"id","cancelled_at"}));
the original statement never changes. Status ``expired`` is worked out on read.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

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
                       if d.cancel_statement else None),
            "inherited": bool(getattr(d, "inherited", False)), "confirmed_by": getattr(d, "confirmed_by", None)}


ACK_TAG = "directive-ack"


async def acknowledgements(session, directives_: list[Directive]) -> dict[str, dict]:
    """Per directive (#306): how many members it was meant for, and who received,
    accepted or declined it (with reasons), read from members' ``directive-ack`` records."""
    from server.models import Member, Record, SwarmMember
    from server.services import recall

    if not directives_:
        return {}
    oldest = min(d.issued_at for d in directives_)
    rows = list(await session.scalars(
        select(Record).where(Record.type == "communication", Record.received_at >= oldest,
                             recall._json_list_contains(session, Record.tags, ACK_TAG))
        .order_by(Record.id)))
    payloads = await recall.fetch_payloads(session, rows)
    latest: dict[str, dict[str, dict]] = {}
    received: dict[str, set] = {}
    for r in rows:
        body = getattr(payloads.get(r.id), "body", None) or {}
        did = body.get("directive")
        if not did:
            continue
        received.setdefault(did, set()).add(r.agent)
        if body.get("status") in ("accepted", "declined"):
            latest.setdefault(did, {})[r.agent] = {"status": body["status"], "reason": body.get("reason")}
    members_by_swarm: dict[str | None, int] = {}
    for sid, n in (await session.execute(
            select(SwarmMember.swarm_id, func.count()).join(Member, Member.id == SwarmMember.member_id)
            .where(SwarmMember.left_at.is_(None), Member.status == "active").group_by(SwarmMember.swarm_id))).all():
        members_by_swarm[sid] = n
    out = {}
    for d in directives_:
        did = str(d.id)
        decided = latest.get(did, {})
        out[did] = {
            "meant_for": members_by_swarm.get(d.scope_swarm_id, 0) if d.scope_swarm_id else sum(members_by_swarm.values()),
            "received": len(received.get(did, ())),
            "accepted": sum(1 for v in decided.values() if v["status"] == "accepted"),
            "declined": sum(1 for v in decided.values() if v["status"] == "declined"),
            "declines": [{"member": m, "reason": v["reason"]} for m, v in sorted(decided.items())
                         if v["status"] == "declined"],
        }
    return out


async def list_all(session, limit: int = 100) -> list[dict]:
    rows = list(await session.scalars(select(Directive).order_by(Directive.issued_at.desc()).limit(limit)))
    acks = await acknowledgements(session, rows)
    return [{**await to_dict(session, d), "delivery": acks.get(str(d.id))} for d in rows]


async def inherit_from(session, swarm, leader_name: str | None) -> int:
    """Leader change (#310): the old leader's open directives for this swarm become
    ``inherited``, waiting for the new leader to confirm or cancel each one."""
    if not leader_name:
        return 0
    n = 0
    for d in await session.scalars(select(Directive).where(
            Directive.scope_swarm_id == swarm.id, Directive.signer == "leader",
            Directive.issued_by == leader_name, Directive.status == "open")):
        if status_of(d) == "open" and not d.inherited:
            d.inherited = True
            n += 1
    return n


async def issue_as_leader(session, cfg, leader, body: dict) -> dict:
    """A swarm's current leader issues a directive for its own swarm (#310).

    The leader signs it with its own key: ``{"statement": <JCS text>, "signature": <hex>}``.
    The server checks the leader is current, the statement is well formed and canonical,
    addressed to the leader's swarm only, fresh, and signed by the leader.
    """
    import json as _json

    from nacl.signing import VerifyKey

    swarm = await session.scalar(select(Swarm).where(Swarm.leader_member_id == leader.id, Swarm.status == "active"))
    if swarm is None:
        raise ServiceError(403, "not_leader", "only a swarm's current leader can issue its directives")
    raw, sig = body.get("statement") if isinstance(body, dict) else None, body.get("signature") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not isinstance(sig, str):
        raise ServiceError(422, "bad_directive", "send {statement, signature}, signed with the leader's key")
    try:
        stmt = _json.loads(raw)
        canonical = canonicalize(stmt).decode("utf-8") == raw
    except Exception:  # noqa: BLE001
        raise ServiceError(422, "bad_directive", "the statement isn't JSON") from None
    if not canonical:
        raise ServiceError(422, "bad_directive", "the statement must be JCS (canonical JSON)")
    if stmt.get("scope") != {"swarm": swarm.id}:
        raise ServiceError(403, "not_your_swarm", "a leader can only issue directives to its own swarm")
    if stmt.get("v") != 1 or stmt.get("issued_by") != leader.name:
        raise ServiceError(422, "bad_directive", "v must be 1 and issued_by the leader's name")
    text = stmt.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ServiceError(422, "bad_directive", f"text is required, at most {MAX_TEXT} characters")
    try:
        did = uuid.UUID(str(stmt.get("id")))
        issued = datetime.fromisoformat(str(stmt["issued_at"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(stmt["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        raise ServiceError(422, "bad_directive", "id, issued_at and expires_at are required") from None
    now = _now()
    if abs((issued - now).total_seconds()) > 300 or not issued < expires <= issued + timedelta(days=MAX_DAYS):
        raise ServiceError(422, "bad_directive", "issued_at must be now (±5 min) and expires_at within 90 days")
    try:
        VerifyKey(bytes(leader.public_key)).verify(DOMAIN.encode() + raw.encode(), bytes.fromhex(sig))
    except Exception:  # noqa: BLE001
        raise ServiceError(422, "bad_signature", "the signature doesn't match the leader's key") from None
    if await session.get(Directive, did) is not None:
        raise ServiceError(409, "duplicate", "a directive with this id exists")
    d = Directive(id=did, scope_swarm_id=swarm.id, text=text.strip(), payload=stmt.get("payload"),
                  issued_by=leader.name, issued_at=issued, expires_at=expires, status="open",
                  statement=raw, signature=sig, signer="leader")
    session.add(d)
    session.add(AuditLog(actor=leader.name, member_id=leader.id, action="directive_issue",
                         detail={"directive": str(did), "scope": {"swarm": swarm.id}, "signer": "leader"}))
    await session.flush()
    return await to_dict(session, d)


async def _leader_swarm(session, leader) -> Swarm:
    swarm = await session.scalar(select(Swarm).where(Swarm.leader_member_id == leader.id, Swarm.status == "active"))
    if swarm is None:
        raise ServiceError(403, "not_leader", "only a swarm's current leader can do this")
    return swarm


async def inherited_for(session, leader) -> list[dict]:
    swarm = await _leader_swarm(session, leader)
    rows = await session.scalars(select(Directive).where(
        Directive.scope_swarm_id == swarm.id, Directive.inherited.is_(True), Directive.status == "open"))
    return [await to_dict(session, d) for d in rows if status_of(d) == "open"]


async def leader_decides(session, cfg, leader, directive_id, action: str) -> dict:
    """The new leader confirms or cancels an inherited directive (#310). Audited."""
    swarm = await _leader_swarm(session, leader)
    d = await get(session, directive_id)
    if d.scope_swarm_id != swarm.id:
        raise ServiceError(403, "not_your_swarm", "that directive isn't for your swarm")
    if action == "confirm":
        if not d.inherited:
            raise ServiceError(409, "not_inherited", "only an inherited directive needs confirming")
        d.inherited, d.confirmed_by = False, leader.name
        session.add(AuditLog(actor=leader.name, member_id=leader.id, action="directive_confirm",
                             detail={"directive": str(d.id)}))
        await session.flush()
        return await to_dict(session, d)
    if d.signer != "leader":
        raise ServiceError(403, "fleet_directive", "a leader can't cancel the fleet's directives")
    # The cancel statement is signed with the fleet key: LTMS attests the current leader asked.
    return await cancel(session, cfg, d.id, actor=leader.name)
