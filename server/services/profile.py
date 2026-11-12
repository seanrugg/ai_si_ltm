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
from server.services import cognition, recall


from server.models import AuditLog, Member, Record, RecordProvenance, Swarm, SwarmMember
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

    # Most active members (swarm subject only): the top authors, by category (#192).
    members = []
    if subject.type == "swarm":
        per_member: dict[str, dict] = defaultdict(_zero)
        for agent, rtype, n in (await session.execute(
                select(Record.agent, Record.type, func.count()).where(where)
                .group_by(Record.agent, Record.type))).all():
            if CATEGORY_OF.get(rtype):
                per_member[agent][CATEGORY_OF[rtype]] += n
        ranked = sorted(per_member.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))[:TOP_PEERS]
        members = [{"name": name, **cats} for name, cats in ranked]

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
        "members": members,
        "lessons": lessons,
    }


# --------------------------------------------------------------------------- #
# Interaction graph (#187)
# --------------------------------------------------------------------------- #
async def graph(session: AsyncSession, subject: Subject, hours: int) -> dict:
    """Nodes, per-category edges and hub relays for the Interactions tab.

    Edges are undirected (``a`` < ``b``), one per pair and category. A record
    relayed by a hub that isn't its author is a ``relays`` entry, never an edge.
    For a swarm, edges touching any current member are included, so outside
    members it talks to appear too.
    """
    since = _since(hours)
    edges = [{"a": a, "b": b, "category": cat, "count": n}
             for (a, b, cat), n in sorted((await edge_counts(session, subject, since)).items())]

    relays = []
    if subject.member_ids:
        hub = Member.__table__.alias("hub")
        author = Member.__table__.alias("author")
        rows = (await session.execute(
            select(hub.c.name, author.c.name, func.count())
            .select_from(Record)
            .join(RecordProvenance, RecordProvenance.record_id == Record.id)
            .join(hub, hub.c.id == RecordProvenance.relay_member_id)
            .join(author, author.c.id == Record.member_id)
            .where(_in_range(since), RecordProvenance.relay_member_id.is_not(None),
                   RecordProvenance.relay_member_id != Record.member_id,
                   or_(Record.member_id.in_(subject.member_ids),
                       RecordProvenance.relay_member_id.in_(subject.member_ids)))
            .group_by(hub.c.name, author.c.name).order_by(hub.c.name, author.c.name))).all()
        relays = [{"hub": h, "member": m, "count": n} for h, m, n in rows]

    names = sorted({e["a"] for e in edges} | {e["b"] for e in edges}
                   | {r["hub"] for r in relays} | {r["member"] for r in relays} | set(subject.member_names))
    members = {m.name: m for m in await session.scalars(select(Member).where(Member.name.in_(names)))} if names else {}
    memberships = {}
    if members:
        for row, swarm_name in (await session.execute(
                select(SwarmMember, Swarm.name).join(Swarm, Swarm.id == SwarmMember.swarm_id)
                .where(SwarmMember.member_id.in_([m.id for m in members.values()]),
                       SwarmMember.left_at.is_(None)))).all():
            memberships[row.member_id] = (row.role, swarm_name)
    nodes = []
    for name in names:
        m = members.get(name)
        role, swarm = memberships.get(m.id, ("free", None)) if m is not None else ("free", None)
        nodes.append({"name": name, "role": role, "kind": m.kind if m is not None else None,
                      "cognition": cognition.resolve(m) if m is not None else None,
                      "is_hub": role == "hub", "swarm": swarm})
    return {"nodes": nodes, "edges": edges, "relays": relays}


# Senses (#188): one entry per sensor, from sensed records' payloads
# --------------------------------------------------------------------------- #
MAX_SENSED_SCAN = 5000
SERIES_POINTS = 48
SERIES_HOURS = 24


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _derived_from(refs) -> list[str]:
    if not isinstance(refs, dict):
        return []
    value = refs.get("derived_from")
    return [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str)]


async def senses(session: AsyncSession, subject: Subject, hours: int) -> dict:
    """Sensors seen in the subject's sensed records (perceptions tagged telemetry).

    Scans at most the newest MAX_SENSED_SCAN (5,000) sensed records per call,
    so a busy sensor's ``records`` count is a lower bound beyond that. A sensor
    is named by ``payload.sensor``, else ``payload.kind`` (docs/sensing.md).
    ``modality``, ``unit`` and ``rate_hz`` come from its newest payload.
    ``series`` averages the last 24 h of numeric samples into at most 48
    points. A record whose payload was purged is counted under a
    ``sensor: null`` entry for its member and contributes nothing else.
    """
    since = _since(hours)
    where = and_(subject.records(), _in_range(since), Record.type == "perception",
                 recall._json_list_contains(session, Record.tags, "telemetry"))
    rows = list(await session.scalars(
        select(Record).where(where).order_by(Record.ts.desc(), Record.id.desc()).limit(MAX_SENSED_SCAN)))
    payloads = await recall.fetch_payloads(session, rows)

    now = datetime.now(timezone.utc)
    series_start = now - timedelta(hours=SERIES_HOURS)
    bucket = timedelta(hours=SERIES_HOURS) / SERIES_POINTS
    sensors: dict[tuple, dict] = {}
    by_record: dict[str, tuple] = {}  # "agent:seq" -> sensor key
    for r in rows:  # newest first
        row = payloads.get(r.id)
        body = row.body if row is not None else None
        name = None
        if isinstance(body, dict):
            name = body.get("sensor") or body.get("kind")
            name = name if isinstance(name, str) else None
        key = (r.agent, name)
        s = sensors.get(key)
        if s is None:
            s = sensors[key] = {"member": r.agent, "sensor": name, "modality": None, "unit": None,
                                "rate_hz": None, "last": None, "_sums": defaultdict(lambda: [0.0, 0]),
                                "records": 0, "feeds": 0}
            if name is not None:  # newest payload for this sensor
                s["modality"], s["unit"], s["rate_hz"] = body.get("modality"), body.get("unit"), body.get("rate_hz")
        s["records"] += 1
        if name is None:
            continue
        by_record[f"{r.agent}:{r.seq}"] = key
        samples = [x for x in body.get("samples") or [] if isinstance(x, dict) and "v" in x]
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
        if s["last"] is None and samples:
            newest = max(samples, key=lambda x: x.get("t") if _is_number(x.get("t")) else 0)
            t = newest.get("t") if _is_number(newest.get("t")) else 0
            s["last"] = {"ts": (ts + timedelta(seconds=t)).isoformat(), "value": newest["v"]}
        for x in samples:
            t = x.get("t") if _is_number(x.get("t")) else 0
            at = ts + timedelta(seconds=t)
            if _is_number(x["v"]) and series_start <= at <= now:
                i = min(int((at - series_start) / bucket), SERIES_POINTS - 1)
                s["_sums"][i][0] += x["v"]
                s["_sums"][i][1] += 1

    # feeds: perceived records (not telemetry) derived from one of the sensor's records
    if by_record:
        perceived = await session.execute(
            select(Record.refs).where(
                subject.records(), _in_range(since), Record.type == "perception", Record.refs.is_not(None),
                or_(Record.tags.is_(None), ~recall._json_list_contains(session, Record.tags, "telemetry"))))
        for (refs,) in perceived.all():
            for key in {by_record[v] for v in _derived_from(refs) if v in by_record}:
                sensors[key]["feeds"] += 1

    out = []
    for s in sorted(sensors.values(), key=lambda s: (s["member"], s["sensor"] is None, s["sensor"] or "")):
        sums = s.pop("_sums")
        s["series"] = [{"t": (series_start + bucket * i).isoformat(), "v": total / n}
                       for i, (total, n) in sorted(sums.items())]
        out.append(s)
    return {"sensors": out}


# --------------------------------------------------------------------------- #
# History (#188): membership, learning and admin events, newest first
# --------------------------------------------------------------------------- #
MAX_EVENTS = 200
HISTORY_KINDS = {
    "enroll_request": "membership", "enroll_auto": "membership", "enroll_approve": "membership",
    "swarm_join": "membership", "swarm_leave": "membership", "permissions_change": "membership",
    "cognition_change": "membership", "member_revoke": "membership",
    "memory_wipe": "admin", "record_delete": "admin", "network_change": "admin",
    "swarm_appoint_hub": "membership",
}
WHERE_AI = {"onboard": "on board", "offboard": "off board"}


def _event_text(action: str, d: dict, swarm_names: dict,
                member_names: dict | None = None) -> tuple[str, str | None]:
    """(title, detail) for one audit row: short English built from its detail."""
    d = d if isinstance(d, dict) else {}
    member_names = member_names or {}
    if action in ("enroll_request", "enroll_auto"):
        swarm = f" to {d['swarm']}" if d.get("swarm") else ""
        return (f"Enrolled{swarm}" if action == "enroll_auto" else f"Asked to enroll{swarm}",
                f"as a {d['kind']}" if d.get("kind") else None)
    if action == "enroll_approve":
        perms = ", ".join(d.get("permissions") or []) or "no permissions"
        return "Enrollment approved", f"with {perms}"
    if action == "swarm_join":
        verb = "Moved to" if d.get("moved_from") else "Joined"
        return f"{verb} {d.get('swarm')}", f"as {d['role']}" if d.get("role") else None
    if action == "swarm_appoint_hub":
        old = member_names.get(d.get("replaced"))
        return f"Appointed hub of {d.get('swarm')}", f"replacing {old}" if old else None
    if action == "swarm_appoint_hub_replaced":
        return f"Replaced as hub of {d.get('swarm')}", f"by {d['hub']}" if d.get("hub") else None
    if action == "swarm_leave":
        name = swarm_names.get(d.get("swarm"), d.get("swarm"))
        return f"Left {name}", None
    if action == "permissions_change":
        after = ", ".join(((d.get("after") or {}).get("permissions")) or []) or "none"
        return "Permissions changed", f"now {after}"
    if action == "cognition_change":
        by = {"admin": "set by an admin", "declared": "declared by device", "observed": "observed"}.get(d.get("source"))
        if d.get("new") is None:
            return "AI location override cleared", None
        return f"AI location: {WHERE_AI.get(d['new'], d['new'])}", by
    if action == "member_revoke":
        return "Revoked", d.get("reason")
    if action == "memory_wipe":
        return "Memory wiped", f"wipe {d['generation']}" if d.get("generation") else None
    if action == "record_delete":
        return f"Deleted record {d.get('agent')}:{d.get('seq')}", d.get("reason")
    if action == "network_change":
        return "Address changed", f"{d.get('old')} → {d.get('new')}" if d.get("old") else d.get("new")
    return action.replace("_", " ").capitalize(), None


async def history(session: AsyncSession, subject: Subject) -> dict:
    """The subject's life: audit events for it (or each current swarm member), newest first."""
    ids, names = subject.member_ids, set(subject.member_names)
    if not ids:
        return {"events": []}
    # record_delete rows name the agent in `detail`, not `member_id`; match as text, then exactly.
    detail_text = cast(AuditLog.detail, String)
    deletes = and_(AuditLog.action == "record_delete",
                   or_(*[detail_text.like(f'%"agent": "{n}"%') for n in names]))
    # A hub appointment names the hub it replaced in `detail.replaced` (a member id, #278).
    replaced = and_(AuditLog.action == "swarm_appoint_hub",
                    or_(*[detail_text.like(f'%"replaced": "{i}"%') for i in ids]))
    rows = list(await session.scalars(
        select(AuditLog).where(AuditLog.action.in_(list(HISTORY_KINDS)),
                               or_(AuditLog.member_id.in_(ids), deletes, replaced))
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(MAX_EVENTS)))
    swarm_names = dict((await session.execute(select(Swarm.id, Swarm.name))).all())
    replaced_ids = {(a.detail or {}).get("replaced") for a in rows if a.action == "swarm_appoint_hub"} - {None}
    member_names = {}
    if replaced_ids:
        wanted = []
        for i in replaced_ids:
            try:
                wanted.append(uuid.UUID(str(i)))
            except ValueError:
                pass
        if wanted:
            member_names = {str(mid): n for mid, n in (await session.execute(
                select(Member.id, Member.name).where(Member.id.in_(wanted)))).all()}
    id_strs = {str(i) for i in ids}

    events = []
    for a in rows:
        if a.action == "record_delete" and (a.detail or {}).get("agent") not in names and a.member_id not in ids:
            continue
        action = a.action
        if (action == "swarm_appoint_hub" and a.member_id not in ids
                and str((a.detail or {}).get("replaced")) in id_strs):
            action = "swarm_appoint_hub_replaced"  # seen from the hub that was replaced
        title, detail = _event_text(action, a.detail, swarm_names, member_names)
        events.append({"ts": a.ts.isoformat(), "kind": HISTORY_KINDS[a.action], "action": action,
                       "title": title, "detail": detail, "_order": (a.ts, 1)})
    # learning: each member's first lesson
    firsts = (await session.execute(
        select(Record.agent, func.min(Record.id)).where(Record.member_id.in_(ids), Record.type == "lesson")
        .group_by(Record.agent))).all()
    for agent, first_id in firsts:
        r = await session.get(Record, first_id)
        events.append({"ts": r.ts.isoformat(), "kind": "learning", "action": "first_lesson",
                       "title": "First lesson" if subject.type == "member" else f"{agent}'s first lesson",
                       "detail": r.summary, "_order": (r.ts, 0)})

    def when(e):
        ts = e["_order"][0]
        return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc), e["_order"][1])

    events.sort(key=when, reverse=True)
    for e in events:
        e.pop("_order")
    return {"events": events[:MAX_EVENTS]}


# --------------------------------------------------------------------------- #
# What it read (#243): dataset reads and dream cycles
# --------------------------------------------------------------------------- #
NEWEST_CYCLES = 20
CYCLE_TAG = "dream_cycle"


async def reading(session: AsyncSession, subject: Subject, hours: int) -> dict:
    """Datasets the subject read, and its newest dream cycles with their lessons.

    Reads belong to members, so a swarm's reads are its current members'. Cycles
    come from the subject's ``event`` records tagged ``dream_cycle``; a cycle's
    lessons are the subject's lessons whose ``payload.cycle`` matches.
    """
    from server.models import Dataset, DatasetRead  # local: profile predates datasets

    since = _since(hours)
    datasets: list[dict] = []
    if subject.member_ids:
        stmt = (select(DatasetRead.dataset_id, Dataset.name, func.count(DatasetRead.id),
                       func.count(func.distinct(DatasetRead.item_key)), func.max(DatasetRead.ts))
                .join(Dataset, Dataset.id == DatasetRead.dataset_id)
                .where(DatasetRead.member_id.in_(subject.member_ids))
                .group_by(DatasetRead.dataset_id, Dataset.name))
        if since is not None:
            stmt = stmt.where(DatasetRead.ts >= since)
        for did, name, reads, items, last in (await session.execute(stmt)).all():
            datasets.append({"dataset": str(did), "name": name, "reads": int(reads), "items": int(items),
                             "last_read": last.isoformat() if last else None})
        datasets.sort(key=lambda d: (-d["reads"], d["name"]))

    # Cycle events, newest first. The tag test is a text match on the JSON list,
    # which works the same on SQLite and PostgreSQL.
    events = list(await session.scalars(
        select(Record).where(subject.records(), _in_range(since), Record.type == "event",
                             cast(Record.tags, String).like(f'%"{CYCLE_TAG}"%'))
        .order_by(Record.ts.desc(), Record.id.desc()).limit(NEWEST_CYCLES * 4)))
    payloads = await recall.fetch_payloads(session, events)
    cycles: dict[str, dict] = {}
    for ev in events:
        body = payloads[ev.id].body if ev.id in payloads else {}
        cid = body.get("cycle") if isinstance(body, dict) else None
        if not isinstance(cid, str):
            continue
        c = cycles.setdefault(cid, {"cycle": cid, "agent": ev.agent, "started": None, "ended": None,
                                    "items_read": None, "lessons": []})
        if body.get("phase") == "start":
            c["started"] = ev.ts.isoformat()
        elif body.get("phase") == "end":
            c["ended"] = ev.ts.isoformat()
            if isinstance(body.get("items_read"), int):
                c["items_read"] = body["items_read"]
    newest = sorted(cycles.values(), key=lambda c: c["started"] or c["ended"] or "", reverse=True)[:NEWEST_CYCLES]
    wanted = {c["cycle"]: c for c in newest}

    if wanted:
        oldest = min((c["started"] or c["ended"]) for c in newest)
        lessons = list(await session.scalars(
            select(Record).where(subject.records(), Record.type == "lesson",
                                 Record.ts >= datetime.fromisoformat(oldest))
            .order_by(Record.ts, Record.id)))
        lesson_payloads = await recall.fetch_payloads(session, lessons)
        for rec in lessons:
            body = lesson_payloads[rec.id].body if rec.id in lesson_payloads else {}
            cid = body.get("cycle") if isinstance(body, dict) else None
            if cid in wanted:
                wanted[cid]["lessons"].append({"agent": rec.agent, "seq": rec.seq, "summary": rec.summary})

    return {"subject": subject.as_dict(), "hours": hours, "datasets": datasets, "cycles": newest}
