"""Admin endpoints (``ltms_admin`` session cookie). Back the dashboard views."""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select

from server.api.deps import ADMIN_COOKIE, AdminAuth, client_ip, current_admin, transaction
from server.api.schemas import AdminLogin, ApproveRequest, PermissionsRequest, ReasonRequest
from server.models import AuditLog, Member, Record, RecordPayload, RecordProvenance, Swarm
from server.services import auth as auth_service
from server.services import enrollment, integrity, recall, swarms
from server.services.errors import ServiceError

router = APIRouter(prefix="/api", tags=["admin"])
ADMIN = Depends(current_admin)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ServiceError(404, "not_found", "member not found") from None


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #
@router.post("/auth/admin/login")
async def admin_login(body: AdminLogin, request: Request, response: Response) -> dict:
    ip = client_ip(request) or "unknown"
    if not request.app.state.limiters["admin_login"].allow(ip):
        raise ServiceError(429, "rate_limited", "too many login attempts; wait a minute")
    cfg = request.app.state.config
    async with transaction(request) as session:
        raw, expires = await auth_service.admin_login(
            session, username=body.username, password=body.password,
            ttl=cfg.admin_session_ttl_seconds, remote_ip=client_ip(request),
        )
    response.set_cookie(
        ADMIN_COOKIE, raw, max_age=cfg.admin_session_ttl_seconds, httponly=True,
        secure=cfg.cookie_secure, samesite="strict", path="/api",
    )
    return {"username": body.username, "expires_at": expires.isoformat()}


@router.post("/auth/admin/logout")
async def admin_logout(request: Request, response: Response) -> dict:
    async with transaction(request) as session:
        await auth_service.admin_logout(session, request.cookies.get(ADMIN_COOKIE))
    response.delete_cookie(ADMIN_COOKIE, path="/api")
    return {"ok": True}


@router.get("/admin/me")
async def me(admin: AdminAuth = ADMIN) -> dict:
    return {"username": admin.username}


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #
@router.get("/admin/members")
async def list_members(request: Request, status: str | None = None, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        stmt = select(Member).order_by(Member.created_at.desc())
        if status:
            stmt = stmt.where(Member.status == status)
        members = [enrollment.member_to_dict(m) for m in await session.scalars(stmt)]
        return {"members": members}


@router.get("/admin/members/pending")
async def pending_members(request: Request, admin: AdminAuth = ADMIN) -> dict:
    return await list_members(request, status="pending", admin=admin)


@router.post("/admin/members/{member_id}/approve")
async def approve(member_id: str, body: ApproveRequest, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        m = await enrollment.approve(session, _uuid(member_id), admin=admin.username,
                                     permissions=body.permissions, scope=body.scope)
        return enrollment.member_to_dict(m)


@router.post("/admin/members/{member_id}/reject")
async def reject(member_id: str, body: ReasonRequest, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        m = await enrollment.reject(session, _uuid(member_id), admin=admin.username, reason=body.reason)
        return enrollment.member_to_dict(m)


@router.post("/admin/members/{member_id}/revoke")
async def revoke(member_id: str, body: ReasonRequest, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        m = await enrollment.revoke(session, _uuid(member_id), admin=admin.username, reason=body.reason)
        return enrollment.member_to_dict(m)


@router.patch("/admin/members/{member_id}/permissions")
async def set_permissions(member_id: str, body: PermissionsRequest, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        m = await enrollment.set_permissions(session, _uuid(member_id), admin=admin.username,
                                             permissions=body.permissions, scope=body.scope)
        return enrollment.member_to_dict(m)


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
@router.get("/admin/timeline")
async def timeline(
    request: Request,
    admin: AdminAuth = ADMIN,
    type: str | None = None,
    agent: str | None = None,
    place: str | None = None,
    peer: str | None = None,
    tag: str | None = None,
    outcome: str | None = None,
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    q: str | None = None,
    exclude_tag: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    f = recall.RecordFilter(type=type, agent=agent, place=place, peer=peer, tag=tag, exclude_tag=exclude_tag,
                            outcome=outcome, since=since, until=until, q=q, limit=limit, offset=offset)
    async with transaction(request) as session:
        records = await recall.query_records(session, f, recall.Scope())
        return {"records": records, "count": len(records), "limit": limit, "offset": offset}


@router.get("/admin/volume")
async def volume(
    request: Request, days: int = Query(14, ge=1, le=90), admin: AdminAuth = ADMIN,
) -> dict:
    """Records and payload bytes per day, member and type over the last `days` days.

    Grouping happens in SQL. `payload_bytes` sums RecordPayload.size, so a purged
    payload counts as 0 bytes.
    """
    async with transaction(request) as session:
        if session.bind.dialect.name == "postgresql":
            day = func.to_char(func.date_trunc("day", Record.ts), "YYYY-MM-DD")
        else:
            day = func.date(Record.ts)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(
                day.label("day"),
                Record.agent,
                Record.type,
                func.count(Record.id).label("records"),
                func.coalesce(func.sum(RecordPayload.size), 0).label("payload_bytes"),
            )
            .outerjoin(RecordPayload, RecordPayload.record_id == Record.id)
            .where(Record.ts >= since)
            .group_by(day, Record.agent, Record.type)
            .order_by(day.desc(), Record.agent, Record.type)
        )
        rows = (await session.execute(stmt)).all()
        return {
            "days": days,
            "rows": [
                {"day": r.day, "agent": r.agent, "type": r.type, "records": r.records, "payload_bytes": r.payload_bytes}
                for r in rows
            ],
        }


@router.get("/admin/records/{agent}/{seq}")
async def record_detail(agent: str, seq: int, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        rec = await session.scalar(select(Record).where(Record.agent == agent, Record.seq == seq))
        if rec is None:
            raise ServiceError(404, "not_found", "record not found")
        payloads = await recall.fetch_payloads(session, [rec])
        out = recall.to_wire(rec, payloads.get(rec.id))
        prov = await session.scalar(select(RecordProvenance).where(RecordProvenance.record_id == rec.id))
        if prov is not None:
            swarm_name = None
            if prov.swarm_id is not None:
                swarm_name = await session.scalar(select(Swarm.name).where(Swarm.id == prov.swarm_id))
            relay_name = None
            if prov.relay_member_id is not None:
                relay_name = await session.scalar(select(Member.name).where(Member.id == prov.relay_member_id))
            out["provenance"] = {
                "swarm": swarm_name,
                "relay": relay_name,
                "received_at": prov.received_at.isoformat(),
            }
        else:
            out["provenance"] = {"swarm": None, "relay": None, "received_at": None}
        return out


# --------------------------------------------------------------------------- #
# Swarms
# --------------------------------------------------------------------------- #
@router.get("/admin/swarms")
async def list_swarms(request: Request, admin: AdminAuth = ADMIN) -> list[dict]:
    async with transaction(request) as session:
        return await swarms.describe(session)


@router.post("/admin/swarms")
async def create_swarm_endpoint(request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        swarm = await swarms.create_swarm(
            session,
            name=body.get("name"),
            public_key_hex=body.get("public_key"),
            description=body.get("description"),
            is_home=bool(body.get("home")),
            actor=f"admin:{admin.username}",
        )
        return {"id": swarm.id, "name": swarm.name, "status": swarm.status, "home": swarm.is_home,
                "description": swarm.description, "members": []}


@router.post("/admin/swarms/{name}/members")
async def add_swarm_member(name: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        swarm = await swarms.get_swarm(session, name)
        member = await session.scalar(select(Member).where(Member.name == body.get("member")))
        if member is None:
            raise ServiceError(404, "no_member", f"no member named {body.get('member')!r}")
        row = await swarms.set_member(session, member, swarm, body.get("role"),
                                      actor=f"admin:{admin.username}")
        return {"swarm": swarm.name, "member": member.name, "role": row.role}


@router.delete("/admin/swarms/members/{member}")
async def remove_swarm_member(member: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        m = await session.scalar(select(Member).where(Member.name == member))
        if m is None:
            raise ServiceError(404, "no_member", f"no member named {member!r}")
        removed = await swarms.remove_member(session, m, actor=f"admin:{admin.username}")
        return {"member": m.name, "removed": removed}


@router.post("/admin/swarms/{name}/hub")
async def appoint_swarm_hub(name: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        swarm = await swarms.get_swarm(session, name)
        row = await swarms.appoint_hub(session, swarm, body, actor=f"admin:{admin.username}")
        member = await session.get(Member, row.member_id)
        return {"swarm": swarm.name, "hub": member.name}


@router.post("/admin/swarms/{name}/revoke")
async def revoke_swarm_endpoint(name: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        swarm = await swarms.get_swarm(session, name)
        released = await swarms.revoke_swarm(session, swarm, actor=f"admin:{admin.username}")
        return {"swarm": swarm.name, "revoked": True, "members_released": released}


@router.get("/admin/conversation")
async def conversation(
    request: Request, member_a: str, member_b: str, limit: int = Query(200, ge=1, le=1000), admin: AdminAuth = ADMIN
) -> dict:
    """Communications between two members, oldest first."""
    async with transaction(request) as session:
        peers = recall._json_list_contains
        stmt = (
            select(Record)
            .where(Record.type == "communication")
            .where(or_(
                (Record.agent == member_a) & peers(session, Record.peers, member_b),
                (Record.agent == member_b) & peers(session, Record.peers, member_a),
            ))
            .order_by(Record.ts.desc(), Record.id.desc())
            .limit(limit)
        )
        rows = list(await session.scalars(stmt))[::-1]
        payloads = await recall.fetch_payloads(session, rows)
        return {"records": [recall.to_wire(r, payloads.get(r.id)) for r in rows]}


def _ref_values(refs: dict | None) -> set[str]:
    out: set[str] = set()
    for v in (refs or {}).values():
        out.update(v if isinstance(v, list) else [v])
    return out


@router.get("/admin/decision-trace")
async def decision_trace(request: Request, agent: str, seq: int, admin: AdminAuth = ADMIN) -> dict:
    """A record plus everything that references it (``refs`` value ``"<agent>:<seq>"``),
    two levels deep: decision -> actions -> follow-up outcome records."""
    async with transaction(request) as session:
        root = await session.scalar(select(Record).where(Record.agent == agent, Record.seq == seq))
        if root is None:
            raise ServiceError(404, "not_found", "record not found")
        window = select(Record).where(
            Record.ts >= root.ts - timedelta(minutes=5), Record.ts <= root.ts + timedelta(days=1),
            Record.refs.is_not(None),
        ).order_by(Record.ts).limit(2000)
        candidates = list(await session.scalars(window))
        children: dict[str, list[Record]] = defaultdict(list)
        for c in candidates:
            for ref in _ref_values(c.refs):
                children[ref].append(c)
        level1 = children.get(f"{agent}:{seq}", [])
        level2 = [g for c in level1 for g in children.get(f"{c.agent}:{c.seq}", [])]
        # Inputs: what the root itself references.
        inputs = []
        for ref in _ref_values(root.refs):
            a, _, s = ref.rpartition(":")
            if a and s.isdigit():
                r = await session.scalar(select(Record).where(Record.agent == a, Record.seq == int(s)))
                if r is not None:
                    inputs.append(r)
        all_rows = [root, *inputs, *level1, *level2]
        payloads = await recall.fetch_payloads(session, all_rows)
        wire = lambda rows: [recall.to_wire(r, payloads.get(r.id)) for r in rows]  # noqa: E731
        return {"root": wire([root])[0], "inputs": wire(inputs), "children": wire(level1), "grandchildren": wire(level2)}


@router.get("/admin/outcomes")
async def outcomes(
    request: Request, period_days: int | None = Query(30, ge=1), agent: str | None = None,
    place: str | None = None, admin: AdminAuth = ADMIN,
) -> dict:
    """Outcome counts for actions, grouped by member, action summary and place."""
    async with transaction(request) as session:
        key = func.lower(func.coalesce(Record.summary, ""))
        stmt = select(Record.agent, key, Record.place, Record.outcome, func.count()).where(
            Record.type == "action", Record.outcome.is_not(None)
        )
        if period_days:
            stmt = stmt.where(Record.ts >= datetime.now(timezone.utc) - timedelta(days=period_days))
        if agent:
            stmt = stmt.where(Record.agent == agent)
        if place:
            stmt = stmt.where(Record.place == place)
        stmt = stmt.group_by(Record.agent, key, Record.place, Record.outcome)
        groups: dict[tuple, dict] = {}
        for a, summary, pl, oc, n in (await session.execute(stmt)).all():
            g = groups.setdefault((a, summary, pl), {"agent": a, "action": summary, "place": pl, "total": 0, "outcomes": {}})
            g["outcomes"][oc] = n
            g["total"] += n
        rows = sorted(groups.values(), key=lambda g: -g["total"])
        for g in rows:
            g["success_rate"] = round(g["outcomes"].get("success", 0) / g["total"], 3) if g["total"] else None
        return {"period_days": period_days, "groups": rows}


@router.get("/admin/lessons")
async def lessons(request: Request, limit: int = Query(100, ge=1, le=500), admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        les = await recall.query_records(session, recall.RecordFilter(type="lesson", limit=limit), recall.Scope())
        pol = await recall.query_records(session, recall.RecordFilter(type="policy", limit=limit), recall.Scope())
        return {"lessons": les, "policies": pol}


@router.get("/admin/integrity")
async def integrity_view(request: Request, member: str | None = None, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        if member:
            m = await session.scalar(select(Member).where(Member.name == member))
            if m is None:
                raise ServiceError(404, "not_found", "member not found")
            return {"members": [await integrity.verify_member(session, m)]}
        return {"members": await integrity.verify_all(session)}


@router.get("/admin/recall-log")
async def recall_log(
    request: Request, action: str = "recall", member: str | None = None,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), admin: AdminAuth = ADMIN,
) -> dict:
    """Audit log, recall entries by default (``action=all`` for everything)."""
    async with transaction(request) as session:
        stmt = select(AuditLog).order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
        if action != "all":
            stmt = stmt.where(AuditLog.action == action)
        if member:
            stmt = stmt.where(AuditLog.actor == member)
        rows = await session.scalars(stmt)
        return {"entries": [
            {"id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action,
             "member_id": str(r.member_id) if r.member_id else None, "detail": r.detail,
             "request_id": r.request_id, "response_count": r.response_count}
            for r in rows
        ]}


@router.get("/admin/settings")
async def settings(request: Request, admin: AdminAuth = ADMIN) -> dict:
    """Effective configuration, without secrets. Editing comes in P4."""
    cfg = request.app.state.config
    hidden = {"db_password", "admin_password", "database_url"}
    return {k: v for k, v in json.loads(cfg.model_dump_json()).items() if k not in hidden}
