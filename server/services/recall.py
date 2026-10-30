"""Recall: filtered queries and situation "context packs".

Every member-facing recall goes through :func:`effective_scope`, so a member
holding only ``recall:scoped`` never sees records outside its granted scope,
whatever filters it sends.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ltms_contract import RECORD_TYPES
from sqlalchemy import cast, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from server.models import Record, RecordPayload, RecordProvenance, Swarm
from server.services.errors import ServiceError


class RecallDenied(Exception):
    pass


@dataclass
class Scope:
    """None for a field means unrestricted."""

    types: list[str] | None = None
    agents: list[str] | None = None
    places: list[str] | None = None
    swarms: list[str] | None = None


def effective_scope(permissions: list[str], scope: dict | None) -> Scope:
    """Full scope with ``recall``; the granted scope with only ``recall:scoped``
    (an empty scope then means nothing is visible); RecallDenied otherwise."""
    if "recall" in permissions:
        return Scope()
    if "recall:scoped" in permissions:
        # Deny by default: "types" is a required allowlist, so a scope that lists
        # no types (or no scope at all) sees nothing. agents/places/swarms narrow it further.
        s = scope or {}
        return Scope(types=s.get("types", []), agents=s.get("agents"), places=s.get("places"),
                     swarms=s.get("swarms"))
    raise RecallDenied("member lacks a recall permission")


def _json_list_contains(session: AsyncSession, column, value: str):
    """Portable "JSON array column contains value"."""
    if session.bind.dialect.name == "postgresql":
        return column.op("@>")(cast(literal(json.dumps([value])), JSONB))
    each = func.json_each(column).table_valued("value").alias()
    return exists(select(literal(1)).select_from(each).where(each.c.value == value))


def apply_scope(stmt: Select, scope: Scope) -> Select:
    if scope.types is not None:
        stmt = stmt.where(Record.type.in_(scope.types))
    if scope.agents is not None:
        stmt = stmt.where(Record.agent.in_(scope.agents))
    if scope.places is not None:
        stmt = stmt.where(Record.place.in_(scope.places))
    if scope.swarms is not None:
        stmt = stmt.where(Record.id.in_(
            select(RecordProvenance.record_id)
            .join(Swarm, Swarm.id == RecordProvenance.swarm_id)
            .where(Swarm.name.in_(scope.swarms))))
    return stmt


@dataclass
class RecordFilter:
    type: str | None = None          # comma-separated list, e.g. "thought,action"
    agent: str | None = None
    place: str | None = None
    peer: str | None = None
    tag: str | None = None
    exclude_tag: str | None = None   # drop records carrying this tag
    outcome: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    q: str | None = None
    limit: int = 25
    offset: int = 0

    def types(self) -> list[str]:
        """The comma-separated ``type`` as a list, blanks dropped."""
        return [t.strip() for t in (self.type or "").split(",") if t.strip()]


def build_query(session: AsyncSession, f: RecordFilter, scope: Scope) -> Select:
    stmt = select(Record)
    if f.type:
        wanted = f.types()
        if wanted:
            unknown = [t for t in wanted if t not in RECORD_TYPES]
            if unknown:
                raise ServiceError(
                    422, "bad_type",
                    f"unknown type(s): {', '.join(unknown)}; must be one of {', '.join(RECORD_TYPES)}",
                )
            stmt = stmt.where(Record.type.in_(wanted))
    if f.agent:
        stmt = stmt.where(Record.agent == f.agent)
    if f.place:
        stmt = stmt.where(Record.place == f.place)
    if f.outcome:
        stmt = stmt.where(Record.outcome == f.outcome)
    if f.since:
        stmt = stmt.where(Record.ts >= f.since)
    if f.until:
        stmt = stmt.where(Record.ts < f.until)
    if f.peer:
        stmt = stmt.where(_json_list_contains(session, Record.peers, f.peer))
    if f.tag:
        stmt = stmt.where(_json_list_contains(session, Record.tags, f.tag))
    if f.exclude_tag:
        # tags may be SQL NULL; "NOT (NULL @> ...)" is NULL, so keep untagged rows
        stmt = stmt.where(or_(Record.tags.is_(None), ~_json_list_contains(session, Record.tags, f.exclude_tag)))
    if f.q:
        stmt = stmt.where(func.lower(func.coalesce(Record.summary, "")).contains(f.q.lower()))
    return apply_scope(stmt, scope)


async def fetch_payloads(session: AsyncSession, records: list[Record]) -> dict[int, RecordPayload]:
    if not records:
        return {}
    rows = await session.scalars(
        select(RecordPayload).where(RecordPayload.record_id.in_([r.id for r in records]))
    )
    return {p.record_id: p for p in rows}


def to_wire(record: Record, payload: RecordPayload | None, *, include_payload: bool = True) -> dict:
    """The record exactly as the member signed it (re-verifiable by anyone with
    the member's public key), plus ``payload`` or a purge marker."""
    out = json.loads(record.header_raw)
    out["hash"] = record.hash
    out["sig"] = record.sig
    if include_payload:
        if payload is not None:
            out["payload"] = json.loads(payload.payload_raw)
        else:
            out["payload"] = None
            out["payload_purged"] = True
    return out


async def query_records(
    session: AsyncSession, f: RecordFilter, scope: Scope, *, include_payload: bool = True
) -> list[dict]:
    stmt = build_query(session, f, scope).order_by(Record.ts.desc(), Record.id.desc())
    stmt = stmt.limit(f.limit).offset(f.offset)
    rows = list(await session.scalars(stmt))
    payloads = await fetch_payloads(session, rows) if include_payload else {}
    return [to_wire(r, payloads.get(r.id), include_payload=include_payload) for r in rows]


CONTEXT_CAP = 25


async def context_pack(
    session: AsyncSession,
    scope: Scope,
    *,
    intent: str | None,
    target: str | None,
    place: str | None,
    agent: str | None = None,
    now: datetime | None = None,
) -> dict:
    """A small bundle for "I'm about to <intent> <target> in <place>".

    * ``outcomes``  – last 10 actions matching intent/target/place that carry an outcome
    * ``comms``     – the target's communications (or ones naming it) in the last 24 h
    * ``lessons``   – lessons touching the target, place or intent
    * ``policies``  – the newest policies
    Capped at 25 records total, in that priority order.
    """
    now = now or datetime.now(timezone.utc)

    def base(**kw) -> Select:
        return build_query(session, RecordFilter(**kw), scope)

    outcomes_q = base(type="action", place=place, peer=target, q=intent, agent=agent).where(
        Record.outcome.is_not(None)
    )
    comm_conds = []
    if target:
        comm_conds = [Record.agent == target, _json_list_contains(session, Record.peers, target)]
    comms_q = base(type="communication", since=now - timedelta(hours=24))
    if comm_conds:
        comms_q = comms_q.where(or_(*comm_conds))
    lesson_conds = []
    if target:
        lesson_conds.append(_json_list_contains(session, Record.peers, target))
    if place:
        lesson_conds.append(Record.place == place)
    if intent:
        lesson_conds.append(func.lower(func.coalesce(Record.summary, "")).contains(intent.lower()))
    lessons_q = base(type="lesson")
    if lesson_conds:
        lessons_q = lessons_q.where(or_(*lesson_conds))
    policies_q = base(type="policy")

    sections: list[tuple[str, Select, int]] = [
        ("outcomes", outcomes_q, 10),
        ("comms", comms_q, 5),
        ("lessons", lessons_q, 5),
        ("policies", policies_q, 5),
    ]
    pack: dict[str, list] = {}
    remaining = CONTEXT_CAP
    for name, stmt, limit in sections:
        take = min(limit, remaining)
        if take <= 0:
            pack[name] = []
            continue
        rows = list(await session.scalars(stmt.order_by(Record.ts.desc(), Record.id.desc()).limit(take)))
        payloads = await fetch_payloads(session, rows)
        pack[name] = [to_wire(r, payloads.get(r.id)) for r in rows]
        remaining -= len(rows)
    pack["count"] = CONTEXT_CAP - remaining
    pack["query"] = {"intent": intent, "target": target, "place": place}
    return pack
