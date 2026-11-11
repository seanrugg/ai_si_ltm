"""Profile data for a member or a swarm: summary and interaction graph (#186, #187).

Every profile endpoint takes exactly one subject (``member=`` or ``swarm=``)
and ``hours`` (default 168, ``0`` = all time, at most 8760). A swarm's records
are those whose provenance swarm is that swarm, plus records by its current
members. Counting is grouped in SQL; only the interaction edges read record
headers, and only those with peers or refs that touch the subject.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Member, Record, RecordProvenance, Swarm, SwarmMember
from server.services import recall
from server.services.errors import ServiceError

DEFAULT_HOURS = 168
MAX_HOURS = 8760
WEEKLY_ABOVE_DAYS = 90
CATEGORIES = ("sense", "act", "comm", "learn")
CATEGORY_OF = {
    "perception": "sense",
    "thought": "act", "decision": "act", "action": "act", "event": "act",
    "communication": "comm",
    "lesson": "learn", "policy": "learn",
}  # `system` belongs to no category
OUTCOMES = ("success", "partial", "reflex_stop", "stuck", "abandoned", "failed")
FLOW = {"thought": "reasoned", "decision": "decided", "action": "performed", "lesson": "learned"}
TOP_PLACES = 10
TOP_PEERS = 8
NEWEST_LESSONS = 5


def _zero() -> dict:
    return {c: 0 for c in CATEGORIES}


@dataclass
class Subject:
    type: str                      # "member" | "swarm"
    name: str
    member_ids: list[uuid.UUID] = field(default_factory=list)
    member_names: list[str] = field(default_factory=list)
    swarm_id: str | None = None

    def as_dict(self) -> dict:
        return {"type": self.type, "name": self.name}

    def records(self):
        """SQL condition: the subject's records."""
        own = Record.member_id.in_(self.member_ids) if self.member_ids else None
        if self.type == "member":
            return own
        relayed = Record.id.in_(select(RecordProvenance.record_id).where(RecordProvenance.swarm_id == self.swarm_id))
        return or_(relayed, own) if own is not None else relayed


def parse_hours(hours: int | None) -> int:
    if hours is None:
        return DEFAULT_HOURS
    if not isinstance(hours, int) or hours < 0 or hours > MAX_HOURS:
        raise ServiceError(422, "bad_hours", f"hours must be 0 (all time) to {MAX_HOURS}")
    return hours


async def resolve_subject(session: AsyncSession, member: str | None, swarm: str | None) -> Subject:
    if bool(member) == bool(swarm):
        raise ServiceError(422, "bad_subject", "give exactly one of member= or swarm=")
    if member:
        m = await session.scalar(select(Member).where(Member.name == member))
        if m is None:
            raise ServiceError(404, "not_found", f"no member {member!r}")
        return Subject("member", m.name, [m.id], [m.name])
    s = await session.scalar(select(Swarm).where(Swarm.name == swarm))
    if s is None:
        raise ServiceError(404, "not_found", f"no swarm {swarm!r}")
    rows = (await session.execute(
        select(Member.id, Member.name).join(SwarmMember, SwarmMember.member_id == Member.id)
        .where(SwarmMember.swarm_id == s.id, SwarmMember.left_at.is_(None)).order_by(Member.name)
    )).all()
    return Subject("swarm", s.name, [r.id for r in rows], [r.name for r in rows], s.id)


def _since(hours: int) -> datetime | None:
    return datetime.now(timezone.utc) - timedelta(hours=hours) if hours else None


def _in_range(since: datetime | None):
    return Record.ts >= since if since is not None else True


def _day_expr(session: AsyncSession):
    if session.bind.dialect.name == "postgresql":
        return func.to_char(func.date_trunc("day", func.timezone("UTC", Record.ts)), "YYYY-MM-DD")
    return func.date(Record.ts)


# --------------------------------------------------------------------------- #
# Interactions (#187): who exchanges what with whom
# --------------------------------------------------------------------------- #
def _ref_agents(refs) -> list[str]:
    """Agents named by a record's ``refs`` values (``"agent:seq"`` or lists of them)."""
    out = []
    if not isinstance(refs, dict):
        return out
    for value in refs.values():
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str) and ":" in v:
                agent, _, seq = v.rpartition(":")
                if agent and seq.isdigit():
                    out.append(agent)
    return out


async def edge_counts(session: AsyncSession, subject: Subject, since: datetime | None) -> Counter:
    """``Counter{(a, b, category): count}``, undirected (a < b), for edges touching the subject.

    1. a communication record links its author and each peer (``comm``);
    2. a record whose refs point at another author's record links the two,
       in the referencing record's category.
    """
    names = set(subject.member_names)
    if not names:
        return Counter()
    in_range = _in_range(since)
    touches_peers = or_(*[recall._json_list_contains(session, Record.peers, n) for n in names])
    comm = (await session.execute(
        select(Record.agent, Record.peers).where(
            in_range, Record.type == "communication", Record.peers.is_not(None),
            or_(Record.agent.in_(names), touches_peers))
    )).all()
    # Refs are matched as text first (cheap, may over-match), then parsed exactly.
    refs_text = cast(Record.refs, String)
    mentions = or_(*[refs_text.like(f'%"{n}:%') for n in names])
    refd = (await session.execute(
        select(Record.agent, Record.type, Record.refs).where(
            in_range, Record.refs.is_not(None), or_(Record.agent.in_(names), mentions))
    )).all()

    counts: Counter = Counter()

    def add(x: str, y: str, category: str | None) -> None:
        if category is None or x == y or (x not in names and y not in names):
            return
        a, b = sorted((x, y))
        counts[(a, b, category)] += 1

    for agent, peers in comm:
        for peer in peers if isinstance(peers, list) else []:
            if isinstance(peer, str):
                add(agent, peer, "comm")
    for agent, rtype, refs in refd:
        for other in set(_ref_agents(refs)):
            add(agent, other, CATEGORY_OF.get(rtype))
    return counts


# --------------------------------------------------------------------------- #
# Summary (#186)
# --------------------------------------------------------------------------- #
def _buckets(start: date, end: date, weekly: bool) -> list[date]:
    if weekly:
        start = start - timedelta(days=start.weekday())  # weeks start on Monday
    step = timedelta(days=7 if weekly else 1)
    out, d = [], start
    while d <= end:
        out.append(d)
        d += step
    return out


async def summary(session: AsyncSession, subject: Subject, hours: int) -> dict:
    since = _since(hours)
    where = and_(subject.records(), _in_range(since))
    telemetry = recall._json_list_contains(session, Record.tags, "telemetry")

    by_type = dict((await session.execute(
        select(Record.type, func.count()).where(where).group_by(Record.type))).all())
    sensed = await session.scalar(
        select(func.count()).select_from(Record).where(where, Record.type == "perception", telemetry)) or 0

    categories = _zero()
    for rtype, n in by_type.items():
        if CATEGORY_OF.get(rtype):
            categories[CATEGORY_OF[rtype]] += n
    flow = {"sensed": sensed, "perceived": by_type.get("perception", 0) - sensed,
            **{label: by_type.get(t, 0) for t, label in FLOW.items()}}
    flow = {k: flow[k] for k in ("sensed", "perceived", "reasoned", "decided", "performed", "learned")}

    outcomes = {o: 0 for o in OUTCOMES}
    action_outcomes = Counter()
    for rtype, outcome, n in (await session.execute(
            select(Record.type, Record.outcome, func.count()).where(where, Record.outcome.is_not(None))
            .group_by(Record.type, Record.outcome))).all():
        if outcome in outcomes:
            outcomes[outcome] += n
        if rtype == "action":
            action_outcomes[outcome] += n
    with_outcome = sum(action_outcomes.values())
    action_success = action_outcomes["success"] / with_outcome if with_outcome else None

    # Daily (or weekly) category counts, zero-filled across the range.
    day = _day_expr(session)
    daily_rows = (await session.execute(
        select(day.label("day"), Record.type, func.count()).where(where).group_by(day, Record.type))).all()
    today = datetime.now(timezone.utc).date()
    if since is not None:
        first = since.date()
    else:
        days_seen = [date.fromisoformat(str(r[0])[:10]) for r in daily_rows]
        first = min(days_seen) if days_seen else today
    weekly = (today - first).days + 1 > WEEKLY_ABOVE_DAYS
    keys = _buckets(first, today, weekly)
    daily = {k: _zero() for k in keys}
    for d, rtype, n in daily_rows:
        cat = CATEGORY_OF.get(rtype)
        if cat is None:
            continue
        d = date.fromisoformat(str(d)[:10])
        if weekly:
            d = d - timedelta(days=d.weekday())
        if d in daily:
            daily[d][cat] += n

    # Places: top 10 by total, each with category counts and action success.
    place_totals: dict[str, dict] = defaultdict(lambda: {"total": 0, **_zero()})
    for place, rtype, n in (await session.execute(
            select(Record.place, Record.type, func.count()).where(where, Record.place.is_not(None))
            .group_by(Record.place, Record.type))).all():
        p = place_totals[place]
        p["total"] += n
        if CATEGORY_OF.get(rtype):
            p[CATEGORY_OF[rtype]] += n
    place_actions: dict[str, Counter] = defaultdict(Counter)
    for place, outcome, n in (await session.execute(
            select(Record.place, Record.outcome, func.count())
            .where(where, Record.place.is_not(None), Record.type == "action", Record.outcome.is_not(None))
            .group_by(Record.place, Record.outcome))).all():
        place_actions[place][outcome] += n
    places = []
    for place, p in sorted(place_totals.items(), key=lambda kv: (-kv[1]["total"], kv[0]))[:TOP_PLACES]:
        acts = place_actions.get(place, Counter())
        total_acts = sum(acts.values())
        places.append({"place": place, **p, "action_success": acts["success"] / total_acts if total_acts else None})

    # Peers (member subject only): the top members it exchanged with.
    peers = []
    if subject.type == "member":
        per_peer: dict[str, dict] = defaultdict(_zero)
        for (a, b, cat), n in (await edge_counts(session, subject, since)).items():
            other = b if a == subject.name else a
            per_peer[other][cat] += n
        ranked = sorted(per_peer.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))[:TOP_PEERS]
        peers = [{"name": name, **cats} for name, cats in ranked]

    lessons = [
        {"agent": r.agent, "seq": r.seq, "summary": r.summary, "ts": r.ts.isoformat()}
        for r in await session.scalars(
            select(Record).where(where, Record.type == "lesson")
            .order_by(Record.ts.desc(), Record.id.desc()).limit(NEWEST_LESSONS))
    ]

    return {
        "subject": subject.as_dict(),
        "hours": hours,
        "categories": categories,
        "action_success": action_success,
        "daily": [{"day": d.isoformat(), **counts} for d, counts in daily.items()],
        "places": places,
        "outcomes": outcomes,
        "flow": flow,
        "peers": peers,
        "lessons": lessons,
    }
