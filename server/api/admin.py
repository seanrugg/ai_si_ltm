"""Admin endpoints (``ltms_admin`` session cookie). Back the dashboard views."""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, false, func, Integer, literal, or_, select

from ltms_contract import RECORD_TYPES

from server.api.deps import ADMIN_COOKIE, AdminAuth, client_ip, current_admin, transaction
from server.api.schemas import RetentionUpdate  # noqa: I001  (own line: keeps parallel PRs mergeable)
from server.api.schemas import (AdminLogin, ApproveRequest, BulkMembersRequest,
                                EnrollmentPolicyUpdate, InviteCreate, McpSettingsUpdate,
                                McpTokenCreate, PermissionsRequest, ReasonRequest,
                                SwarmMode)
from server.models import (AuditLog, EnrollmentSwarmRequest, Member, McpToken, Record, RecordPayload,
                           RecordProvenance, RuntimeSetting, Swarm, SwarmMember)
from server.services import auth as auth_service
from server.services import conversation as conversation_service, enrollment, integrity, lrs as lrs_service, mcp_access, policy_lifecycle, recall, runtime_settings, swarms
from server.services import export as export_service
from server.services import records as records_service
from server.services import cognition, memory_wipe
from server.services import location as location_service
from server.services import network as network_service
from server.services import profile as profile_service
from server.services import datasets as datasets_service
from server.services import swarm_remove
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
async def list_members(request: Request, status: str | None = None, free_agent: bool = False, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        stmt = select(Member).order_by(Member.created_at.desc())
        if status:
            stmt = stmt.where(Member.status == status)
        if free_agent:
            stmt = stmt.where(Member.id.not_in(
                select(SwarmMember.member_id).where(SwarmMember.left_at.is_(None))))
        members = [enrollment.member_to_dict(m) for m in await session.scalars(stmt)]
        return {"members": members}


@router.get("/admin/members/pending")
async def pending_members(request: Request, admin: AdminAuth = ADMIN) -> dict:
    return await list_members(request, status="pending", admin=admin)


@router.post("/admin/members/{member_id}/approve")
async def approve(member_id: str, body: ApproveRequest, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        m = await enrollment.approve(session, _uuid(member_id), admin=admin.username,
                                     permissions=body.permissions, scope=body.scope,
                                     join_swarm=body.join_swarm)
        return enrollment.member_to_dict(m)


@router.get("/admin/members/{member_id}/swarm-request")
async def swarm_request(member_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    """The swarm a pending member asked to join, or null (admin-only)."""
    async with transaction(request) as session:
        await enrollment.get_member(session, _uuid(member_id))  # 404 if no such member
        req = await session.get(EnrollmentSwarmRequest, _uuid(member_id))
        if req is None:
            return {"swarm": None}
        swarm = await session.get(Swarm, req.swarm_id)
        return {"swarm": swarm.name if swarm is not None else None}


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


@router.post("/admin/members/{member_id}/wipe")
async def wipe_member(member_id: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    """Memory wipe (issue #157): the member stops recalling anything before now. No undo."""
    async with transaction(request) as session:
        m = await memory_wipe.wipe_member(session, _uuid(member_id), admin=admin.username,
                                          confirm=body.get("confirm"))
        return enrollment.member_to_dict(m)


@router.patch("/admin/members/{member_id}/cognition")
async def set_cognition(member_id: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    """Override where a member's AI runs (#185); ``{"cognition": null}`` clears the override."""
    if "cognition" not in body:
        raise ServiceError(422, "bad_cognition", "body needs cognition: 'onboard', 'offboard' or null")
    async with transaction(request) as session:
        m = await session.get(Member, _uuid(member_id))
        if m is None:
            raise ServiceError(404, "not_found", "no such member")
        await cognition.set_admin(session, m, body["cognition"], admin=f"admin:{admin.username}")
        return enrollment.member_to_dict(m)


@router.post("/admin/members/{member_id}/cognition/observe")
async def observe_cognition(member_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    """Measure where a robot's decisions are made, now (#199). Other kinds: 422."""
    async with transaction(request) as session:
        m = await session.get(Member, _uuid(member_id))
        if m is None:
            raise ServiceError(404, "not_found", "no such member")
        if m.kind != "robot":
            raise ServiceError(422, "not_a_robot", "only robots are observed")
        await cognition.observe(session, m)
        return enrollment.member_to_dict(m)


@router.patch("/admin/members/{member_id}/permissions")
async def set_permissions(member_id: str, body: PermissionsRequest, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        m = await enrollment.set_permissions(session, _uuid(member_id), admin=admin.username,
                                             permissions=body.permissions, scope=body.scope)
        return enrollment.member_to_dict(m)


@router.post("/admin/members/bulk")
async def bulk_members(request: Request, body: BulkMembersRequest, admin: AdminAuth = ADMIN) -> dict:
    """Approve or reject several members at once, one try/except per id, one
    commit at the end. Bulk approve grants each request's own requested
    permissions and never widens them."""
    if body.action not in ("approve", "reject"):
        raise ServiceError(422, "bad_action", "action must be 'approve' or 'reject'")
    results: list[dict] = []
    async with transaction(request) as session:
        for id_str in body.ids:
            try:
                member_id = _uuid(id_str)
                if body.action == "approve":
                    await enrollment.approve(session, member_id, admin=admin.username)
                else:
                    await enrollment.reject(session, member_id, admin=admin.username, reason=body.reason)
                results.append({"id": id_str, "ok": True})
            except ServiceError as exc:
                results.append({"id": id_str, "error": exc.code})
    return {"results": results}


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def _timeline_type_clause(session, type_param: str | None):
    """The ``type`` filter for /admin/timeline only (issue #181).

    ``sensed`` is a perception tagged telemetry; ``perception`` is one that is
    not. An empty or missing list is today's default: everything except
    sensed; ``none`` alone matches nothing. Each selection is a type condition, plus a tag condition for the
    two perception cases, OR'd together. ``RecordFilter`` and ``build_query``
    keep their plain-type semantics for every other caller.
    """
    wanted = [t.strip() for t in (type_param or "").split(",") if t.strip()]
    if "none" in wanted:
        # The Types picker's "None" (issue #205): nothing selected, nothing shown.
        if len(wanted) > 1:
            raise ServiceError(422, "bad_type", "type=none can't be combined with other types")
        return false()
    unknown = [t for t in wanted if t not in RECORD_TYPES and t != "sensed"]
    if unknown:
        raise ServiceError(
            422, "bad_type",
            f"unknown type(s): {', '.join(unknown)}; must be one of {', '.join(RECORD_TYPES)}, sensed",
        )
    groups = wanted or list(RECORD_TYPES)  # empty = everything except sensed
    conds = []
    for t in groups:
        if t == "sensed":
            conds.append(and_(
                Record.type == "perception",
                recall._json_list_contains(session, Record.tags, "telemetry"),
            ))
        elif t == "perception":
            # tags may be SQL NULL; "NOT (NULL @> ...)" is NULL, so keep untagged rows
            conds.append(and_(
                Record.type == "perception",
                or_(Record.tags.is_(None), ~recall._json_list_contains(session, Record.tags, "telemetry")),
            ))
        else:
            conds.append(Record.type == t)
    return or_(*conds)


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
    f = recall.RecordFilter(agent=agent, place=place, peer=peer, tag=tag, exclude_tag=exclude_tag,
                            outcome=outcome, since=since, until=until, q=q, limit=limit, offset=offset)
    async with transaction(request) as session:
        # `type` is handled here (not by build_query) so it can accept `sensed`.
        stmt = recall.build_query(session, f, recall.Scope()).where(_timeline_type_clause(session, type))
        stmt = stmt.order_by(Record.ts.desc(), Record.id.desc()).limit(limit).offset(offset)
        rows = list(await session.scalars(stmt))
        payloads = await recall.fetch_payloads(session, rows)
        records = [recall.to_wire(r, payloads.get(r.id)) for r in rows]
        return {"records": records, "count": len(records), "limit": limit, "offset": offset}


@router.get("/admin/profile/summary")
async def profile_summary(request: Request, admin: AdminAuth = ADMIN, member: str | None = None,
                          swarm: str | None = None, hours: int | None = None) -> dict:
    """Categories, trend, places, outcomes and flow for one member or swarm (#186)."""
    hours = profile_service.parse_hours(hours)
    async with transaction(request) as session:
        subject = await profile_service.resolve_subject(session, member, swarm)
        return await profile_service.summary(session, subject, hours)


@router.get("/admin/network")
async def network(request: Request, admin: AdminAuth = ADMIN, swarm: str | None = None,
                  member: str | None = None) -> dict:
    """Addresses each member uses (#189). Admin only; never exported to an LRS."""
    async with transaction(request) as session:
        return await network_service.describe(session, swarm=swarm, member=member)


@router.get("/admin/profile/graph")
async def profile_graph(request: Request, admin: AdminAuth = ADMIN, member: str | None = None,
                        swarm: str | None = None, hours: int | None = None) -> dict:
    """Who exchanges what with whom: nodes, per-category edges and hub relays (#187)."""
    hours = profile_service.parse_hours(hours)
    async with transaction(request) as session:
        subject = await profile_service.resolve_subject(session, member, swarm)
        return await profile_service.graph(session, subject, hours)


@router.get("/admin/profile/senses")
async def profile_senses(request: Request, admin: AdminAuth = ADMIN, member: str | None = None,
                         swarm: str | None = None, hours: int | None = None) -> dict:
    """One entry per sensor in the subject's sensed records (#188)."""
    hours = profile_service.parse_hours(hours)
    async with transaction(request) as session:
        subject = await profile_service.resolve_subject(session, member, swarm)
        return await profile_service.senses(session, subject, hours)


@router.get("/admin/profile/reading")
async def profile_reading(request: Request, admin: AdminAuth = ADMIN, member: str | None = None,
                          swarm: str | None = None, hours: int | None = None) -> dict:
    """Datasets read and dream cycles, with the lessons each cycle wrote (#243)."""
    hours = profile_service.parse_hours(hours)
    async with transaction(request) as session:
        subject = await profile_service.resolve_subject(session, member, swarm)
        return await profile_service.reading(session, subject, hours)


@router.get("/admin/profile/history")
async def profile_history(request: Request, admin: AdminAuth = ADMIN, member: str | None = None,
                          swarm: str | None = None) -> dict:
    """Membership, learning and admin events over the subject's whole life (#188)."""
    async with transaction(request) as session:
        subject = await profile_service.resolve_subject(session, member, swarm)
        return await profile_service.history(session, subject)


@router.get("/admin/locations")
async def locations(request: Request, admin: AdminAuth = ADMIN, swarm: str | None = None,
                    member: str | None = None) -> dict:
    """Where each member is: gps, declared, network, with the best first (#191). Admin only."""
    async with transaction(request) as session:
        return await location_service.describe(session, request.app.state.config, swarm=swarm, member=member)


@router.put("/admin/locations/{subject_type}/{name}")
async def set_location(subject_type: str, name: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    """Declare a member's or a swarm's location: ``{label, room, lat, lon}`` (#191)."""
    async with transaction(request) as session:
        return await location_service.set_declared(session, subject_type, name, body, admin=f"admin:{admin.username}")


@router.delete("/admin/locations/{subject_type}/{name}")
async def delete_location(subject_type: str, name: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        deleted = await location_service.delete_declared(session, subject_type, name, admin=f"admin:{admin.username}")
        return {"deleted": deleted}


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


@router.get("/admin/activity")
async def activity(
    request: Request,
    hours: int = Query(24, ge=1, le=720),
    group: str = Query("swarm", pattern="^(swarm|member)$"),
    swarm: str | None = None,
    admin: AdminAuth = ADMIN,
) -> dict:
    """Aggregate record counts over a window, grouped by swarm or member.

    The window (``hours``, ending now) is split into 24 equal buckets. A
    record's swarm is its provenance swarm at write time; ``swarm_id`` NULL
    or no provenance row means a free agent, reported as ``key`` null.
    """
    async with transaction(request) as session:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=hours)
        since_epoch = since.timestamp()
        bucket_seconds = hours * 3600 // 24

        if session.bind.dialect.name == "postgresql":
            bucket = func.cast(
                func.floor((func.extract("epoch", Record.ts) - literal(since_epoch)) / bucket_seconds),
                Integer,
            )
        else:
            bucket = func.cast(
                (func.strftime("%s", Record.ts) - literal(since_epoch)) / bucket_seconds,
                Integer,
            )

        if group == "swarm":
            key_expr = Swarm.name
        else:
            key_expr = Record.agent

        key_col = key_expr.label("key")
        type_col = Record.type.label("type")
        bucket_col = bucket.label("bucket")
        tele_col = func.coalesce(
            recall._json_list_contains(session, Record.tags, "telemetry"),
            literal(False),
        ).label("is_telemetry")
        count_col = func.count(Record.id).label("count")

        def apply_swarm_filter(stmt: Select) -> Select:
            if group == "member" and swarm is not None:
                if swarm == "none":
                    return stmt.where(RecordProvenance.swarm_id.is_(None))
                return stmt.where(Swarm.name == swarm)
            return stmt

        stmt = (
            select(key_col, type_col, bucket_col, tele_col, count_col)
            .outerjoin(RecordProvenance, RecordProvenance.record_id == Record.id)
            .outerjoin(Swarm, Swarm.id == RecordProvenance.swarm_id)
            .where(Record.ts >= since)
            .where(Record.ts < now)
        )
        stmt = apply_swarm_filter(stmt).group_by(key_col, type_col, bucket_col, tele_col)
        rows = (await session.execute(stmt)).all()

        groups: dict = {}
        for key, rtype, bucket_idx, is_tele, count in rows:
            g = groups.setdefault(
                key,
                {
                    "total": 0,
                    "telemetry": 0,
                    "by_type": {},
                    "series": [0] * 24,
                    "series_by_type": {},
                    "telemetry_series": [0] * 24,
                },
            )
            g["total"] += count
            b = min(max(int(bucket_idx), 0), 23)
            g["series"][b] += count
            g["by_type"][rtype] = g["by_type"].get(rtype, 0) + count
            g["series_by_type"].setdefault(rtype, [0] * 24)[b] += count
            if is_tele:
                g["telemetry"] += count
                g["telemetry_series"][b] += count

        # previous_total: equal window just before, grouped by key only
        prev_since = since - timedelta(hours=hours)
        prev_stmt = (
            select(key_col, func.count(Record.id).label("count"))
            .outerjoin(RecordProvenance, RecordProvenance.record_id == Record.id)
            .outerjoin(Swarm, Swarm.id == RecordProvenance.swarm_id)
            .where(Record.ts >= prev_since)
            .where(Record.ts < since)
        )
        prev_stmt = apply_swarm_filter(prev_stmt).group_by(key_col)
        previous_by_key = {r.key: r.count for r in (await session.execute(prev_stmt)).all()}

        # Include keys that only appear in the previous window.
        for key in previous_by_key:
            groups.setdefault(
                key,
                {
                    "total": 0,
                    "telemetry": 0,
                    "by_type": {},
                    "series": [0] * 24,
                    "series_by_type": {},
                    "telemetry_series": [0] * 24,
                },
            )

        result = []
        for key, g in groups.items():
            result.append(
                {
                    "key": key,
                    "total": g["total"],
                    "previous_total": previous_by_key.get(key, 0),
                    "telemetry": g["telemetry"],
                    "by_type": g["by_type"],
                    "series": g["series"],
                    "series_by_type": g["series_by_type"],
                    "telemetry_series": g["telemetry_series"],
                }
            )
        result.sort(key=lambda g: (-g["total"], g["key"] is None, g["key"] or ""))

        return {
            "hours": hours,
            "bucket_seconds": bucket_seconds,
            "since": since.isoformat(),
            "group": group,
            "groups": result,
        }


@router.get("/admin/records/{agent}/{seq}")
async def record_detail(agent: str, seq: int, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        rec = await session.scalar(select(Record).where(Record.agent == agent, Record.seq == seq))
        if rec is None:
            tomb = await records_service.get_tombstone(session, agent, seq)
            if tomb is not None:  # deleted by an admin (#173): refs to it resolve to this
                return records_service.tombstone_to_dict(tomb)
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


@router.get("/admin/tombstones")
async def list_tombstones(request: Request, admin: AdminAuth = ADMIN, agent: str | None = None,
                          seq_from: int | None = None, seq_to: int | None = None) -> dict:
    """One member's deleted records in a seq range, newest first (#205).

    The timeline calls this once per page instead of looking up each missing seq.
    """
    if not agent:
        raise ServiceError(422, "bad_agent", "agent is required")
    async with transaction(request) as session:
        rows = await records_service.list_tombstones(session, agent, seq_from=seq_from, seq_to=seq_to)
        return {"tombstones": [records_service.tombstone_to_dict(t) for t in rows]}


@router.get("/admin/records/{agent}/{seq}/delete-preview")
async def record_delete_preview(agent: str, seq: int, request: Request, admin: AdminAuth = ADMIN) -> dict:
    """What deleting this record affects: who refers to it, and any policy change (#173)."""
    async with transaction(request) as session:
        return await records_service.delete_preview(session, agent, seq)


@router.delete("/admin/records/{agent}/{seq}")
async def record_delete(agent: str, seq: int, request: Request, admin: AdminAuth = ADMIN,
                        body: dict | None = None) -> dict:
    """Delete one record, leaving a tombstone so the chain stays valid (#173)."""
    reason = (body or {}).get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ServiceError(422, "bad_reason", "reason must be a string")
    async with transaction(request) as session:
        tomb = await records_service.delete_record(session, agent, seq, admin=f"admin:{admin.username}",
                                                   reason=reason)
        return records_service.tombstone_to_dict(tomb)


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


@router.patch("/admin/swarms/{name}")
async def rename_swarm(name: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    """Rename a swarm (#231): ``{"name": "<new>"}``. Its ID, keys and records don't change."""
    async with transaction(request) as session:
        swarm = await swarms.get_swarm(session, name)
        swarm = await swarms.rename_swarm(session, swarm, body.get("name"), actor=f"admin:{admin.username}")
        return {"id": swarm.id, "name": swarm.name, "status": swarm.status, "home": swarm.is_home}


@router.get("/admin/swarms/{name}/remove-preview")
async def remove_swarm_preview(name: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    """Counts for the Remove swarm dialog (#233): current members, records that came through it."""
    async with transaction(request) as session:
        return await swarm_remove.remove_preview(session, await swarms.get_swarm(session, name))


@router.post("/admin/swarms/{name}/remove")
async def remove_swarm(name: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    """Remove a swarm (#232): ``{"confirm": name, "members": "free" | "remove", "records": "keep" | "purge"}``."""
    async with transaction(request) as session:
        swarm = await swarms.get_swarm(session, name)
        return await swarm_remove.remove_swarm(session, swarm, confirm=body.get("confirm"),
                                               members=body.get("members"), records=body.get("records", "keep"),
                                               admin=admin.username)


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


@router.post("/admin/swarms/{name}/invites")
async def create_swarm_invite(name: str, request: Request, body: InviteCreate, admin: AdminAuth = ADMIN) -> dict:
    """Create a one-time enrollment code for a swarm. The plaintext code is returned once."""
    async with transaction(request) as session:
        swarm = await swarms.get_swarm(session, name)
        row, code = await enrollment.create_invite(
            session, swarm, uses=body.uses, expires_at=body.expires_at,
            actor=f"admin:{admin.username}")
    return {"id": str(row.id), "code": code, "uses_left": row.uses_left,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None}


@router.post("/admin/swarms/{name}/mode")
async def set_swarm_mode(name: str, request: Request, body: SwarmMode, admin: AdminAuth = ADMIN) -> dict:
    """Change how members join a swarm and what open-enrolled members get."""
    async with transaction(request) as session:
        swarm = await swarms.get_swarm(session, name)
        swarm = await swarms.set_enrollment_mode(
            session, swarm, body.mode, body.open_permissions, actor=f"admin:{admin.username}")
    return {"swarm": swarm.name, "enrollment_mode": swarm.enrollment_mode,
            "open_permissions": swarm.open_permissions}


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


@router.post("/admin/swarms/{name}/wipe")
async def wipe_swarm(name: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    """Memory wipe for every active member of a swarm (issue #157). No undo."""
    async with transaction(request) as session:
        return await memory_wipe.wipe_swarm(session, name, admin=admin.username, confirm=body.get("confirm"))


@router.get("/admin/conversation")
async def conversation(
    request: Request,
    members: str | None = None,
    member_a: str | None = None,
    member_b: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    admin: AdminAuth = ADMIN,
) -> dict:
    """Communications between members, oldest first.

    ``members=a,b,c`` returns records authored by any listed member whose peers
    include another listed member (max 16). Otherwise ``member_a`` and
    ``member_b`` behave exactly as before.
    """
    async with transaction(request) as session:
        if members is not None:
            member_list = [m.strip() for m in members.split(",") if m.strip()]
            records = await conversation_service.group_conversation(session, member_list, limit)
        elif member_a is not None and member_b is not None:
            records = await conversation_service.member_conversation(session, member_a, member_b, limit)
        else:
            raise ServiceError(422, "missing_members", "provide members= or member_a and member_b")
        return {"records": records}


@router.get("/admin/swarm-conversation")
async def swarm_conversation(
    request: Request,
    swarm: str,
    limit: int = Query(200, ge=1, le=1000),
    before: str | None = None,
    admin: AdminAuth = ADMIN,
) -> dict:
    """A swarm's communication timeline, oldest first.

    ``swarm=free`` returns free-agent communications (provenance swarm null).
    ``before=<ts>,<id>`` pages backward to records older than that point.
    Each record gains ``from_outside`` and ``relayed_by``.
    """
    parsed_before = None
    if before is not None:
        try:
            ts_str, id_str = before.split(",", 1)
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            parsed_before = (ts, int(id_str))
        except (ValueError, TypeError) as exc:
            raise ServiceError(422, "bad_before", "before must be <ts>,<id>") from exc
    async with transaction(request) as session:
        records = await conversation_service.swarm_conversation(
            session, swarm=swarm, limit=limit, before=parsed_before)
        return {"records": records}


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


@router.get("/admin/policies")
async def admin_policies(
    request: Request,
    status: str | None = Query(None, pattern="^(in_force|proposed|retired|all)$"),
    admin: AdminAuth = ADMIN,
) -> dict:
    """Every policy with its lifecycle status; ``?status=`` filters to one bucket.

    Each item gains ``status`` and, for retired policies, ``retired_by`` (the
    ``"agent:seq"`` of the enacted policy that superseded or revoked it).
    """
    async with transaction(request) as session:
        rows = list(
            await session.scalars(
                select(Record).where(Record.type == "policy").order_by(Record.ts.desc(), Record.id.desc())
            )
        )
        payloads = await recall.fetch_payloads(session, rows)
        classification = policy_lifecycle.classify(rows)
        items: list[dict] = []
        for r in rows:
            key = policy_lifecycle.policy_key(r)
            info = classification[key]
            if status and status != "all" and info["status"] != status:
                continue
            item = recall.to_wire(r, payloads.get(r.id))
            item["status"] = info["status"]
            if info["status"] == "retired":
                item["retired_by"] = info["retired_by"]
            items.append(item)
        return {"policies": items, "count": len(items)}


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
    # Never return credentials: anything named like a password, secret or key,
    # plus the database URL (it can embed a password).
    def hidden(k: str) -> bool:
        return k == "database_url" or any(w in k for w in ("password", "secret", "_key"))
    return {k: v for k, v in json.loads(cfg.model_dump_json()).items() if not hidden(k)}


def _mcp_token_to_dict(t: McpToken) -> dict:
    """A token row for the admin API: every field except the hash."""
    return {
        "id": str(t.id),
        "name": t.name,
        "scope": t.scope,
        "owner": t.owner,
        "purpose": t.purpose,
        "use_count": t.use_count,
        "stale": _is_stale(t),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
    }


def _is_stale(t: McpToken) -> bool:
    """True when the token is active and hasn't been used in 90 days.

    Active means not revoked and not expired. A token that was never used is
    stale when created more than 90 days ago; otherwise it's stale when its
    last use is older than 90 days.
    """
    now = datetime.now(timezone.utc)
    if t.revoked_at is not None:
        return False
    if t.expires_at is not None and t.expires_at <= now:
        return False
    cutoff = now - timedelta(days=90)
    if t.last_used_at is not None:
        return t.last_used_at <= cutoff
    return (t.created_at or now) <= cutoff


# --------------------------------------------------------------------------- #
# MCP read access (issue #106)
# --------------------------------------------------------------------------- #
@router.get("/admin/mcp")
async def mcp_settings(request: Request, admin: AdminAuth = ADMIN) -> dict:
    """MCP read mode, open scope and the current tokens (hashes withheld)."""
    cfg = request.app.state.config
    async with transaction(request) as session:
        effective = await runtime_settings.resolve_mcp_access(session, cfg)
        row = await runtime_settings.get_setting(session, runtime_settings.MCP_ACCESS_KEY)
        tokens = [
            _mcp_token_to_dict(t)
            for t in await session.scalars(select(McpToken).order_by(McpToken.created_at.desc()))
        ]
    return {
        "mode": effective["mode"],
        "open_scope": effective["open_scope"],
        "source": effective["source"],
        "locked": cfg.mcp_settings_locked,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "updated_by": row.updated_by if row else None,
        "tokens": tokens,
    }


@router.put("/admin/mcp/settings")
async def put_mcp_settings(request: Request, body: McpSettingsUpdate, admin: AdminAuth = ADMIN) -> dict:
    """Update the dashboard-stored MCP read mode and open scope.

    Widening public access (switching to open, or adding types or swarms to the
    open scope while mode is open) requires ``confirm: "open"``, otherwise 422
    ``confirm_required``. When the server is locked the environment is
    authoritative and writes return 409 ``locked``.
    """
    cfg = request.app.state.config
    if await runtime_settings.is_locked(cfg):
        raise ServiceError(409, "locked", "MCP settings are locked by the server environment")

    mode = body.mode
    if mode not in ("off", "token", "open"):
        raise ServiceError(422, "bad_mode", "mode must be 'off', 'token' or 'open'")

    open_scope = dict(body.open_scope or {})
    # Validate open_scope types against record types.
    types = open_scope.get("types")
    if types is not None:
        if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
            raise ServiceError(422, "bad_scope", "open_scope.types must be a list of record types")
        unknown = [t for t in types if t not in RECORD_TYPES]
        if unknown:
            raise ServiceError(422, "bad_scope", f"unknown record types: {sorted(unknown)}")
    # Normalize to the config shape: types default [], swarms default null.
    open_scope.setdefault("types", [])
    swarms = open_scope.get("swarms")
    if swarms is not None and not isinstance(swarms, list):
        raise ServiceError(422, "bad_scope", "open_scope.swarms must be a list or null")
    # Each entry is a swarm name, or null for free agents.
    if swarms is not None and not all(
        s is None or (isinstance(s, str) and 1 <= len(s) <= 64) for s in swarms
    ):
        raise ServiceError(422, "bad_scope", "open_scope.swarms entries must be swarm names (1-64 characters) or null")
    open_scope.setdefault("swarms", None)
    # Payloads are never part of the open scope.
    open_scope.pop("payloads", None)

    async with transaction(request) as session:
        current = await runtime_settings.resolve_mcp_access(session, cfg)

        def _widening(cur: dict, new: dict) -> bool:
            if new["mode"] == "open" and cur["mode"] != "open":
                return True
            if cur["mode"] == "open" or new["mode"] == "open":
                cur_types = set(cur.get("open_scope", {}).get("types") or [])
                new_types = set(new.get("open_scope", {}).get("types") or [])
                if new_types - cur_types:
                    return True
                cur_swarms = cur.get("open_scope", {}).get("swarms")
                new_swarms = new.get("open_scope", {}).get("swarms")
                if cur_swarms is not None and new_swarms is None:
                    return True
                if cur_swarms is not None and new_swarms is not None:
                    if set(new_swarms) - set(cur_swarms):
                        return True
            return False

        if _widening(current, {"mode": mode, "open_scope": open_scope}):
            if body.confirm != "open":
                raise ServiceError(422, "confirm_required",
                                   "widening public access requires confirm: \"open\"")

        old = {"mode": current["mode"], "open_scope": current["open_scope"],
               "source": current["source"]}
        await runtime_settings.set_setting(
            session, runtime_settings.MCP_ACCESS_KEY,
            {"mode": mode, "open_scope": open_scope},
            updated_by=f"admin:{admin.username}",
        )
        session.add(
            AuditLog(actor=f"admin:{admin.username}", action="mcp_settings_change",
                     detail={"old": old, "new": {"mode": mode, "open_scope": open_scope}})
        )

    return {"mode": mode, "open_scope": open_scope, "source": "dashboard",
            "locked": cfg.mcp_settings_locked}


@router.delete("/admin/mcp/settings")
async def delete_mcp_settings(request: Request, admin: AdminAuth = ADMIN) -> dict:
    """Reset the dashboard-stored MCP settings to the environment default."""
    cfg = request.app.state.config
    if await runtime_settings.is_locked(cfg):
        raise ServiceError(409, "locked", "MCP settings are locked by the server environment")

    async with transaction(request) as session:
        current = await runtime_settings.resolve_mcp_access(session, cfg)
        old = {"mode": current["mode"], "open_scope": current["open_scope"],
               "source": current["source"]}
        await runtime_settings.delete_setting(session, runtime_settings.MCP_ACCESS_KEY)
        session.add(
            AuditLog(actor=f"admin:{admin.username}", action="mcp_settings_reset",
                     detail={"old": old, "new": {"source": "environment"}})
        )

    return {"ok": True, "source": "environment"}


# --------------------------------------------------------------------------- #
# Enrollment policy (issue #129)
# --------------------------------------------------------------------------- #
@router.get("/admin/export")
async def export_records(
    request: Request, admin: AdminAuth = ADMIN,
    format: str = "jsonl", agent: str | None = None, swarm: str | None = None,
    type: str | None = None, since: str | None = None, until: str | None = None,
) -> StreamingResponse:
    """Download records as JSONL (complete and verifiable) or CSV (issue #153)."""
    f = export_service.parse_filter(fmt=format, agent=agent, swarm=swarm, type_=type, since=since, until=until)
    exported_at = datetime.now(timezone.utc)
    async with transaction(request) as session:  # audit before streaming; filters only, never content
        session.add(AuditLog(actor=f"admin:{admin.username}", action="record_export",
                             detail={"format": format, "filters": f.as_dict()}))
    maker = request.app.state.sessionmaker
    body = (export_service.stream_jsonl(maker, f, exported_at) if format == "jsonl"
            else export_service.stream_csv(maker, f))
    filename = f"ltms-export-{exported_at:%Y%m%d}.{format}"
    return StreamingResponse(body, media_type=export_service.FORMATS[format],
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/admin/enrollment/policy")
async def enrollment_policy_get(request: Request, admin: AdminAuth = ADMIN) -> dict:
    """Effective enrollment policy, its source, the lock flag and the refusal
    counter (N refused since <time>)."""
    cfg = request.app.state.config
    async with transaction(request) as session:
        effective = await runtime_settings.resolve_enrollment_policy(session, cfg)
        row = await runtime_settings.get_setting(session, runtime_settings.ENROLLMENT_POLICY_KEY)
    refusals = enrollment.refusal_counter()
    return {
        "policy": effective["policy"],
        "source": effective["source"],
        "locked": cfg.enrollment_policy_locked,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "updated_by": row.updated_by if row else None,
        "refusals": {
            "count": refusals["count"],
            "since": refusals["since"].isoformat() if refusals["since"] else None,
        },
    }


@router.put("/admin/enrollment/policy")
async def enrollment_policy_put(request: Request, body: EnrollmentPolicyUpdate, admin: AdminAuth = ADMIN) -> dict:
    """Change the enrollment policy. Switching to auto_approve requires
    ``confirm: "auto_approve"`` (422 confirm_required). When locked, 409 locked."""
    cfg = request.app.state.config
    if await runtime_settings.is_enrollment_policy_locked(cfg):
        raise ServiceError(409, "locked", "enrollment policy is locked by the server environment")

    policy = body.policy
    if policy not in ("manual", "auto_deny", "auto_approve"):
        raise ServiceError(422, "bad_policy", "policy must be 'manual', 'auto_deny' or 'auto_approve'")
    if policy == "auto_approve" and body.confirm != "auto_approve":
        raise ServiceError(422, "confirm_required",
                           'switching to auto_approve requires confirm: "auto_approve"')

    async with transaction(request) as session:
        current = await runtime_settings.resolve_enrollment_policy(session, cfg)
        await runtime_settings.set_setting(
            session, runtime_settings.ENROLLMENT_POLICY_KEY,
            {"policy": policy}, updated_by=f"admin:{admin.username}")
        session.add(
            AuditLog(actor=f"admin:{admin.username}", action="enrollment_policy_change",
                     detail={"old": {"policy": current["policy"], "source": current["source"]},
                             "new": {"policy": policy, "source": "dashboard"}})
        )

    return {"policy": policy, "source": "dashboard",
            "locked": cfg.enrollment_policy_locked}


def _retention_out(effective: dict, row, cfg) -> dict:
    return {
        "payload_rules": effective["payload_rules"],
        "audit_days": effective["audit_days"],
        "source": effective["source"],
        "locked": runtime_settings.is_retention_locked(cfg),
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "updated_by": row.updated_by if row else None,
    }


@router.get("/admin/retention")
async def retention_get(request: Request, admin: AdminAuth = ADMIN) -> dict:
    """Effective data retention settings, their source and the lock flag (issue #155)."""
    cfg = request.app.state.config
    async with transaction(request) as session:
        effective = await runtime_settings.resolve_retention(session, cfg)
        row = await runtime_settings.get_setting(session, runtime_settings.RETENTION_KEY)
    return _retention_out(effective, row, cfg)


@router.put("/admin/retention")
async def retention_put(request: Request, body: RetentionUpdate, admin: AdminAuth = ADMIN) -> dict:
    """Change data retention. Empty ``payload_rules`` keeps every payload;
    ``audit_days: null`` keeps the audit log forever. Locked → 409."""
    cfg = request.app.state.config
    if runtime_settings.is_retention_locked(cfg):
        raise ServiceError(409, "locked", "data retention is locked by the server environment")
    new = runtime_settings.validate_retention(body.payload_rules, body.audit_days)
    actor = f"admin:{admin.username}"
    async with transaction(request) as session:
        current = await runtime_settings.resolve_retention(session, cfg)
        row = await runtime_settings.set_setting(session, runtime_settings.RETENTION_KEY, new, updated_by=actor)
        session.add(AuditLog(actor=actor, action="retention_change", detail={
            "old": {"payload_rules": current["payload_rules"], "audit_days": current["audit_days"],
                    "source": current["source"]},
            "new": {**new, "source": "dashboard"},
        }))
        effective = await runtime_settings.resolve_retention(session, cfg)
    return _retention_out(effective, row, cfg)


@router.post("/admin/mcp/tokens")
async def create_mcp_token(request: Request, body: McpTokenCreate, admin: AdminAuth = ADMIN) -> dict:
    """Create a read token. The plaintext is returned exactly once."""
    async with transaction(request) as session:
        row, plaintext = await mcp_access.create(
            session,
            name=body.name,
            scope=body.scope,
            expires_at=body.expires_at,
            owner=body.owner,
            purpose=body.purpose,
            actor=f"admin:{admin.username}",
        )
        return {"id": str(row.id), "name": row.name, "token": plaintext}


@router.post("/admin/mcp/tokens/{token_id}/revoke")
async def revoke_mcp_token(token_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        await mcp_access.revoke(session, _uuid(token_id), actor=f"admin:{admin.username}")
        return {"ok": True}


@router.get("/admin/mcp/tokens")
async def list_mcp_tokens(request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        tokens = [
            _mcp_token_to_dict(t)
            for t in await session.scalars(select(McpToken).order_by(McpToken.created_at.desc()))
        ]
    return {"tokens": tokens}


# --------------------------------------------------------------------------- #
# LRS endpoints (issue #59)
# --------------------------------------------------------------------------- #
_CREATE_FIELDS = {"name", "url", "username", "secret", "xapi_version", "scope", "send_reads"}


@router.get("/admin/lrs")
async def list_lrs_endpoints(request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        return {"endpoints": [lrs_service.endpoint_to_dict(e) for e in await lrs_service.list_endpoints(session)]}


@router.post("/admin/lrs")
async def create_lrs_endpoint(request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    data = {k: v for k, v in body.items() if k in _CREATE_FIELDS}
    data.setdefault("name", None)  # missing -> the validator's 422, not a TypeError 500
    data.setdefault("url", None)
    async with transaction(request) as session:
        endpoint = await lrs_service.create_endpoint(
            session, request.app.state.config, actor=f"admin:{admin.username}", **data,
        )
        return lrs_service.endpoint_to_dict(endpoint)


@router.patch("/admin/lrs/{endpoint_id}")
async def update_lrs_endpoint(endpoint_id: str, request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        endpoint = await lrs_service.update_endpoint(
            session, request.app.state.config, endpoint_id,
            actor=f"admin:{admin.username}",
            **{k: v for k, v in body.items() if k in lrs_service.PATCH_FIELDS},
        )
        return lrs_service.endpoint_to_dict(endpoint)


@router.delete("/admin/lrs/{endpoint_id}")
async def delete_lrs_endpoint(endpoint_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        await lrs_service.delete_endpoint(session, endpoint_id, actor=f"admin:{admin.username}")
        return {"ok": True}


@router.post("/admin/lrs/{endpoint_id}/test")
async def test_lrs_endpoint(endpoint_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        endpoint = await lrs_service.get_endpoint(session, endpoint_id)
    async with httpx.AsyncClient() as http:
        return await lrs_service.test_connection(request.app.state.config, endpoint, http)


@router.get("/admin/lrs/{endpoint_id}/status")
async def lrs_endpoint_status(endpoint_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    """Endpoint view plus the backlog of in-scope, unsent records (issue #61)."""
    async with transaction(request) as session:
        endpoint = await lrs_service.get_endpoint(session, endpoint_id)
        data = lrs_service.endpoint_to_dict(endpoint)
        data["backlog"] = await lrs_service.backlog(session, endpoint)
        return data


# --------------------------------------------------------------------------- #
# Fleet datasets (#236, docs/datasets-design.md)
# --------------------------------------------------------------------------- #
@router.post("/admin/datasets", status_code=201)
async def create_dataset(request: Request, body: dict, admin: AdminAuth = ADMIN) -> dict:
    """Create a dataset (``pending``) with an empty version 1: ``{name, description, kind, licence}``."""
    async with transaction(request) as session:
        ds = await datasets_service.create_dataset(session, body, admin=f"admin:{admin.username}")
        return await datasets_service.dataset_dict(session, ds)


@router.get("/admin/datasets")
async def list_datasets(request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        return {"datasets": await datasets_service.list_datasets(session)}


@router.get("/admin/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        return await datasets_service.dataset_dict(session, await datasets_service.get_dataset(session, dataset_id))


@router.post("/admin/datasets/{dataset_id}/versions", status_code=201)
async def add_dataset_version(dataset_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        ds = await datasets_service.get_dataset(session, dataset_id)
        v = await datasets_service.add_version(session, ds, admin=f"admin:{admin.username}")
        return datasets_service.version_dict(v)


@router.post("/admin/datasets/{dataset_id}/versions/{version}/approve")
async def approve_dataset_version(dataset_id: str, version: int, request: Request, admin: AdminAuth = ADMIN) -> dict:
    """Approve a version: every fleet member can then read it."""
    async with transaction(request) as session:
        ds = await datasets_service.get_dataset(session, dataset_id)
        await datasets_service.approve_version(session, ds, version, admin=f"admin:{admin.username}")
        return await datasets_service.dataset_dict(session, ds)


@router.post("/admin/datasets/{dataset_id}/retire")
async def retire_dataset(dataset_id: str, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        ds = await datasets_service.get_dataset(session, dataset_id)
        await datasets_service.retire(session, ds, admin=f"admin:{admin.username}")
        return await datasets_service.dataset_dict(session, ds)



@router.post("/admin/datasets/{dataset_id}/versions/{version}/items")
async def upload_dataset_items(dataset_id: str, version: int, request: Request,
                               files: list[UploadFile] = File(...), prefix: str | None = Form(None),
                               admin: AdminAuth = ADMIN) -> dict:
    """Upload text files into a pending version (#237). Each file's text is extracted."""
    async with transaction(request) as session:
        ds = await datasets_service.get_dataset(session, dataset_id)
        items = await datasets_service.upload_items(session, request.app.state.config, ds, version, files,
                                                    prefix=prefix)
        return {"items": items, "dataset": await datasets_service.dataset_dict(session, ds)}


@router.get("/admin/datasets/{dataset_id}/versions/{version}/items")
async def list_dataset_items(dataset_id: str, version: int, request: Request, admin: AdminAuth = ADMIN) -> dict:
    async with transaction(request) as session:
        ds = await datasets_service.get_dataset(session, dataset_id)
        return {"items": await datasets_service.list_items(session, ds, version)}
