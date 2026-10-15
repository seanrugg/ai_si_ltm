"""Recall: filtered query over a member's records + context-pack composition.

The member recall path (plan §"Member (Authorization)") answers two questions:

1. ``recall_records`` — return a filtered, paginated slice of the member's own
   ``recordpayload`` rows (plus the header rows they chain to).
2. ``recall_context`` — compose a bounded context pack for a situation: recent
   outcomes of the requested action, peer comms in the last 24h, applicable
   lessons, and relevant policies, capped at ``MAX_CONTEXT`` total entries.

Both are scoped to the requesting member — a member never sees another member's
records.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.member import Member
from server.models.record import RecordHeader
from server.models.recordpayload import RecordPayload

MAX_RECALL = 200
MAX_CONTEXT = 25


def _scope_clause(member: Member):
    """SQLAlchemy filter restricting rows to ``member``'s own records."""
    return RecordPayload.member_id == member.id


async def recall_records(
    session: AsyncSession,
    member: Member,
    *,
    type_: str | None = None,
    agent: str | None = None,
    place: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = 25,
) -> list[dict]:
    """Return a member's own records matching the filters, newest first.

    ``limit`` is clamped to ``MAX_RECALL``. Every returned record carries its
    header fields (so callers have ``seq``, ``ts``, ``type``, ``hash``) merged
    with the payload ``body``.
    """
    stmt = select(RecordPayload, RecordHeader).join(
        RecordHeader, RecordPayload.header_id == RecordHeader.id
    ).where(_scope_clause(member))

    if type_ is not None:
        stmt = stmt.where(RecordHeader.type == type_)
    if agent is not None:
        stmt = stmt.where(RecordHeader.agent == agent)
    if place is not None:
        stmt = stmt.where(RecordHeader.place == place)
    if from_ is not None:
        stmt = stmt.where(RecordHeader.ts >= from_)
    if to is not None:
        stmt = stmt.where(RecordHeader.ts <= to)

    stmt = stmt.order_by(RecordHeader.seq.desc()).limit(min(limit, MAX_RECALL))

    rows = await session.scalars(stmt)
    results: list[dict] = []
    for payload, header in rows:
        entry = dict(payload.body) if isinstance(payload.body, dict) else {}
        entry.update(
            {
                "header_id": header.id,
                "member_id": str(header.member_id),
                "agent": header.agent,
                "seq": header.seq,
                "ts": header.ts.isoformat(),
                "type": header.type,
                "summary": header.summary,
                "place": header.place,
                "peers": header.peers or [],
                "body_hash": header.body_hash.hex(),
                "hash": header.hash.hex(),
                "prev_hash": header.prev_hash.hex(),
                "status": payload.status,
            }
        )
        results.append(entry)
    return results


async def recall_context(
    session: AsyncSession,
    member: Member,
    *,
    intent: str | None = None,
    target: str | None = None,
    place: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Compose a bounded context pack for a situation.

    Returns ``{"outcomes": [...], "peer_comms": [...], "lessons": [...],
    "policies": [...], "total": N}`` where ``total`` is capped at
    :data:`MAX_CONTEXT`. Each list carries up to the number of entries of its
    kind that fit in the remaining budget.
    """
    now = now or datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    budget = MAX_CONTEXT

    pack: dict[str, list] = {"outcomes": [], "peer_comms": [], "lessons": [], "policies": []}

    async def _fill(kind: str, stmt) -> None:
        """Query ``stmt`` and append rows to ``pack[kind]`` until budget is spent."""
        nonlocal budget
        if budget <= 0:
            return
        rows = await session.scalars(stmt.limit(budget))
        for payload, header in rows:
            entry = dict(payload.body) if isinstance(payload.body, dict) else {}
            entry.update(
                {
                    "agent": header.agent,
                    "seq": header.seq,
                    "ts": header.ts.isoformat(),
                    "type": header.type,
                    "summary": header.summary,
                    "place": header.place,
                }
            )
            pack[kind].append(entry)
            budget -= 1

    # 1. Recent outcomes of the requested action (the primary recall signal).
    if target is not None:
        stmt = select(RecordPayload, RecordHeader).join(
            RecordHeader, RecordPayload.header_id == RecordHeader.id
        ).where(
            _scope_clause(member),
            RecordHeader.type == "action",
            RecordHeader.place == place if place is not None else True,
        )
        await _fill("outcomes", stmt)

    # 2. Peer communications in the last 24h.
    stmt = select(RecordPayload, RecordHeader).join(
        RecordHeader, RecordPayload.header_id == RecordHeader.id
    ).where(
        _scope_clause(member),
        RecordHeader.type == "communication",
        RecordHeader.ts >= day_ago,
    )
    await _fill("peer_comms", stmt)

    # 3. Applicable lessons.
    stmt = select(RecordPayload, RecordHeader).join(
        RecordHeader, RecordPayload.header_id == RecordHeader.id
    ).where(
        _scope_clause(member),
        RecordHeader.type == "lesson",
    )
    await _fill("lessons", stmt)

    # 4. Relevant policies.
    stmt = select(RecordPayload, RecordHeader).join(
        RecordHeader, RecordPayload.header_id == RecordHeader.id
    ).where(
        _scope_clause(member),
        RecordHeader.type == "policy",
    )
    await _fill("policies", stmt)

    pack["total"] = sum(len(v) for v in pack.values())
    return pack
