"""Member endpoints (``Authorization: Bearer <token>``)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from server.api.deps import MemberAuth, current_member, request_id, require, transaction
from server.api.schemas import RecordBatch
from server.models import AuditLog, Member
from server.services import recall, swarms
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
        )
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
        return {"name": member.name, "last_ack_seq": member.last_ack_seq, "last_hash": member.last_hash}


async def _member_scope(session, auth: MemberAuth) -> recall.Scope:
    member = await session.get(Member, auth.member_id)
    try:
        return recall.effective_scope(auth.permissions, member.scope)
    except recall.RecallDenied as exc:
        raise ServiceError(403, "forbidden", str(exc)) from None


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
    include_payload: bool = True,
    limit: int = Query(25, ge=1),
    offset: int = Query(0, ge=0),
) -> dict:
    cfg = request.app.state.config
    f = recall.RecordFilter(type=type, agent=agent, place=place, peer=peer, tag=tag, outcome=outcome,
                            since=since, until=until, q=summary, limit=min(limit, cfg.max_recall), offset=offset)
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
