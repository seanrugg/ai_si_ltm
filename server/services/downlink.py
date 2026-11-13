"""Downlink (#304): what a hub pulls from LTMS to publish on its swarm's bus.

For the calling hub's swarm, in order: directives addressed to that swarm or to
all swarms (issued, and cancelled), and ``communication`` records addressed to the
swarm (``peers`` contains ``swarm:<topic id>``) from members **outside** it that hold
``speak:swarm``. Records are returned exactly as signed. Nothing older than 7 days.

The cursor is opaque (base64 JSON): the last directive-event second with the events
already delivered in it, and the last record id, so a hub never gets an item twice
or misses one.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from server.models import Directive, Member, Record, Swarm, SwarmMember
from server.services import directives as directives_service
from server.services import recall, swarm_identity, swarms
from server.services.errors import ServiceError

WINDOW = timedelta(days=7)
LIMIT = 200
SPEAK = "speak:swarm"


def _encode(c: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(c, separators=(",", ":")).encode()).decode()


def _decode(cursor: str | None) -> dict:
    if not cursor:
        return {"dir": None, "rec": 0}
    try:
        c = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        d = c.get("dir")
        if d is not None and not (isinstance(d, list) and len(d) == 2 and isinstance(d[0], str)
                                  and isinstance(d[1], list)):
            raise ValueError("bad dir")
        return {"dir": d, "rec": int(c.get("rec") or 0)}
    except (ValueError, TypeError, AttributeError):
        raise ServiceError(422, "bad_cursor", "unknown cursor; start again without one") from None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def pull(session, hub: Member, cursor: str | None, now: datetime | None = None) -> dict:
    swarm_id = await swarms.hub_swarm_id(session, hub)
    if swarm_id is None:
        raise ServiceError(403, "not_hub", "only the current hub of a swarm can pull its downlink")
    now = now or datetime.now(timezone.utc)
    since = now - WINDOW
    c = _decode(cursor)
    swarm = await session.get(Swarm, swarm_id)

    # Directive events: an issue, and (later) a cancel, each its own item.
    events = []
    for d in await session.scalars(select(Directive).where(
            or_(Directive.scope_swarm_id.is_(None), Directive.scope_swarm_id == swarm_id),
            or_(Directive.issued_at >= since, Directive.cancelled_at >= since))):
        if _aware(d.issued_at) >= since:
            events.append((directives_service._iso(d.issued_at), "issue", str(d.id), d))
        if d.cancelled_at is not None and _aware(d.cancelled_at) >= since:
            events.append((directives_service._iso(d.cancelled_at), "cancel", str(d.id), d))
    events.sort(key=lambda e: e[:3])
    # Times are to the second, so the cursor keeps the last second and every event
    # already delivered in it: later events in that same second still come through.
    if c["dir"]:
        t, seen = c["dir"][0], set(c["dir"][1])
        events = [e for e in events if e[0] > t or (e[0] == t and f"{e[1]}:{e[2]}" not in seen)]
    events = events[:LIMIT]
    items = []
    for t, kind, _, d in events:
        if kind == "issue":
            items.append({"kind": "directive", "directive": await directives_service.to_dict(session, d)})
        else:
            items.append({"kind": "cancel", "id": str(d.id), "statement": d.cancel_statement,
                          "signature": d.cancel_signature})
    if events:
        t = events[-1][0]
        prev = set(c["dir"][1]) if c["dir"] and c["dir"][0] == t else set()
        new_dir = [t, sorted(prev | {f"{e[1]}:{e[2]}" for e in events if e[0] == t})]
    else:
        new_dir = c["dir"]

    # Records addressed to the swarm by permitted members outside it.
    topic = await swarm_identity.topic_id(session, swarm)
    inside = select(SwarmMember.member_id).where(SwarmMember.swarm_id == swarm_id, SwarmMember.left_at.is_(None))
    rows = list(await session.scalars(
        select(Record).join(Member, Member.id == Record.member_id).where(
            Record.id > c["rec"], Record.type == "communication", Record.received_at >= since,
            Record.peers.is_not(None), recall._json_list_contains(session, Record.peers, f"swarm:{topic}"),
            recall._json_list_contains(session, Member.permissions, SPEAK),
            and_(Member.status == "active", Member.id.not_in(inside)))
        .order_by(Record.id).limit(LIMIT)))
    payloads = await recall.fetch_payloads(session, rows)
    items += [{"kind": "message", "record": recall.to_wire(r, payloads.get(r.id))} for r in rows]
    new_rec = rows[-1].id if rows else c["rec"]
    return {"swarm": {"id": swarm_id, "topic_id": topic}, "items": items,
            "cursor": _encode({"dir": new_dir, "rec": new_rec}),
            "more": len(events) == LIMIT or len(rows) == LIMIT}
