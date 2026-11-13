"""Member endpoints (``Authorization: Bearer <token>``)."""
from __future__ import annotations

import dataclasses
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from server.api.deps import MemberAuth, client_ip, current_member, request_id, require, transaction
from server.api.schemas import RecordBatch
from server.models import AuditLog, Member
from server.services import charter, cognition, network, recall, swarm_identity, swarms
from server.services.errors import ServiceError
from server.services.ingest import ingest_batch

router = APIRouter(prefix="/api", tags=["member"])


@router.post("/records")
async def post_records(
    body: RecordBatch, request: Request, auth: MemberAuth = Depends(require("contribute"))
) -> dict:
    """Ingest a batch. See ``services/ingest.py`` for the valid-prefix policy."""
    cfg = request.app.state.config
    if len(body.records) > cfg.max_batch:
        raise ServiceError(413, "batch_too_large", f"at most {cfg.max_batch} records per request")
    async with transaction(request) as session:
        result = await ingest_batch(
            session,
            auth.member_id,
            body.records,
            future_skew_seconds=cfg.future_skew_seconds,
            request_id=request_id(request),
            charter_grace_days=cfg.charter_grace_days,
        )
        if result.accepted:  # the submitter's address; a hub's relay records the hub only
            await network.record_seen(session, await session.get(Member, auth.member_id), client_ip(request))
        return result.as_dict()


@router.get("/records/head")
async def chain_head(request: Request, agent: str | None = None,
                     auth: MemberAuth = Depends(current_member)) -> dict:
    """A chain head, so a member can resume after a restart.

    Without ``agent``: the caller's own head. With ``agent``: another member's
    head, only for the hub of that member's swarm (a relay resyncing its queue).
    """
    async with transaction(request) as session:
        member = await session.get(Member, auth.member_id)
        if agent is not None and agent != member.name:
            relay_swarm = await swarms.hub_swarm_id(session, member)
            other = await session.scalar(select(Member).where(Member.name == agent))
            row = await swarms.open_membership(session, other.id) if other is not None else None
            if relay_swarm is None or row is None or row.swarm_id != relay_swarm:
                raise ServiceError(403, "not_in_swarm", f"{agent!r} is not a member of this relay's swarm")
            member = other
        head = {"name": member.name, "last_ack_seq": member.last_ack_seq, "last_hash": member.last_hash,
                "memory_generation": member.memory_generation}
        status = await charter.status_for(session, request.app.state.config, member)
        if status is not None:
            head["charter"] = status  # #255: tells a client when to read the charter
        return head


@router.get("/fleet-key")
async def get_fleet_key(request: Request) -> dict:
    """The fleet's public key (#303), to verify directives. Public: it's a public key."""
    from server.services import fleet_key

    return {"public_key": fleet_key.public_hex(request.app.state.config)}


@router.get("/swarm")
async def my_swarm(request: Request, auth: MemberAuth = Depends(current_member)) -> dict:
    """The caller's swarm (#292): permanent ID, MQTT topic ID, current name and role; null for a free agent."""
    async with transaction(request) as session:
        member = await session.get(Member, auth.member_id)
        return {"swarm": await swarm_identity.swarm_of(session, member)}


@router.put("/members/me/cognition")
async def put_cognition(request: Request, body: dict, auth: MemberAuth = Depends(current_member)) -> dict:
    """The device reports where its AI runs (#185): ``{"location", "models"}``."""
    async with transaction(request) as session:
        member = await session.get(Member, auth.member_id)
        return {"cognition": await cognition.declare(session, member, body)}


@router.put("/members/me/specs")
async def put_specs(request: Request, body: dict, auth: MemberAuth = Depends(current_member)) -> dict:
    """The device reports its spec sheet (#332): hardware, models, software, notes."""
    from server.services import specs

    async with transaction(request) as session:
        member = await session.get(Member, auth.member_id)
        return {"specs": await specs.report(session, member, body)}


@router.put("/members/me/network")
async def put_network(request: Request, body: dict, auth: MemberAuth = Depends(current_member)) -> dict:
    """LAN addresses this member reports (#190). Unverified, and labelled so."""
    async with transaction(request) as session:
        member = await session.get(Member, auth.member_id)
        return {"addresses": await network.record_reported(session, member, body, source="reported")}


@router.get("/charter")
async def read_charter(request: Request, auth: MemberAuth = Depends(current_member)) -> dict:
    """The current fleet charter (#255). Logged as a read, and sent to the LRS."""
    async with transaction(request) as session:
        return await charter.read(session, member_id=auth.member_id)


@router.post("/members/me/reads")
async def report_read(request: Request, body: dict, auth: MemberAuth = Depends(current_member)) -> dict:
    """``{dataset, version, item, cycle?}``: an item this member read itself, usually an
    external one fetched from its source (#248). Logged with ``source: reported``."""
    from server.services import integrations

    async with transaction(request) as session:
        return await integrations.report_read(session, member_id=auth.member_id, body=body)


@router.get("/members/me/pings")
async def get_pings(request: Request, auth: MemberAuth = Depends(current_member)) -> dict:
    """Pending admin pings (#356): this member's own, plus its swarm members' for a hub.
    Fetching marks them delivered. Answer with a heartbeat (``"ping": true``)."""
    from server.services import ping

    async with transaction(request) as session:
        return {"pings": await ping.take_for(session, auth.member_id)}


@router.get("/relay/downlink")
async def relay_downlink(request: Request, since: str | None = None,
                         auth: MemberAuth = Depends(current_member)) -> dict:
    """Directives and swarm-addressed messages for the caller's swarm (#304). Hub only."""
    from server.services import downlink

    async with transaction(request) as session:
        return await downlink.pull(session, await session.get(Member, auth.member_id), since)


@router.put("/relay/network/{agent}")
async def put_relay_network(agent: str, request: Request, body: dict,
                            auth: MemberAuth = Depends(current_member)) -> dict:
    """A hub reports the LAN addresses of a member of its swarm (#190)."""
    async with transaction(request) as session:
        hub = await session.get(Member, auth.member_id)
        relay_swarm = await swarms.hub_swarm_id(session, hub)
        other = await session.scalar(select(Member).where(Member.name == agent))
        row = await swarms.open_membership(session, other.id) if other is not None else None
        if relay_swarm is None or row is None or row.swarm_id != relay_swarm:
            raise ServiceError(403, "not_in_swarm", f"{agent!r} is not a member of this relay's swarm")
        return {"addresses": await network.record_reported(session, other, body, source="hub")}


async def _member_scope(session, auth: MemberAuth) -> recall.Scope:
    member = await session.get(Member, auth.member_id)
    try:
        scope = recall.effective_scope(auth.permissions, member.scope)
    except recall.RecallDenied as exc:
        raise ServiceError(403, "forbidden", str(exc)) from None
    # A memory wipe limits what this member reads, from any author (issue #157).
    return dataclasses.replace(scope, after_id=member.recall_floor_id)


def _audit(session, auth: MemberAuth, request: Request, detail: dict, count: int) -> None:
    session.add(AuditLog(member_id=auth.member_id, actor=auth.name, action="recall",
                         detail=detail, request_id=request_id(request), response_count=count))


@router.get("/records")
async def get_records(
    request: Request,
    auth: MemberAuth = Depends(require("recall", "recall:scoped")),
    type: str | None = None,
    agent: str | None = None,
    place: str | None = None,
    peer: str | None = None,
    tag: str | None = None,
    outcome: str | None = None,
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    summary: str | None = None,
    exclude_tag: str | None = None,
    include_payload: bool = True,
    limit: int = Query(25, ge=1),
    offset: int = Query(0, ge=0),
) -> dict:
    cfg = request.app.state.config
    f = recall.RecordFilter(type=type, agent=agent, place=place, peer=peer, tag=tag, exclude_tag=exclude_tag,
                            outcome=outcome, since=since, until=until, q=summary,
                            limit=min(limit, cfg.max_recall), offset=offset)
    async with transaction(request) as session:
        scope = await _member_scope(session, auth)
        records = await recall.query_records(session, f, scope, include_payload=include_payload)
        _audit(session, auth, request, {"endpoint": "records", **{k: str(v) for k, v in vars(f).items() if v is not None}}, len(records))
        return {"records": records, "count": len(records), "limit": f.limit, "offset": f.offset}


@router.get("/recall/context")
async def get_context(
    request: Request,
    auth: MemberAuth = Depends(require("recall", "recall:scoped")),
    intent: str | None = None,
    target: str | None = None,
    place: str | None = None,
) -> dict:
    async with transaction(request) as session:
        scope = await _member_scope(session, auth)
        pack = await recall.context_pack(session, scope, intent=intent, target=target, place=place)
        _audit(session, auth, request, {"endpoint": "context", "intent": intent, "target": target, "place": place},
               pack["count"])
        return pack
