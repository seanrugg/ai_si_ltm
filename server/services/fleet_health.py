"""Fleet health (#381): one row per active member, for the dashboard's Health page.

Reads only what is already recorded; there is no new telemetry. Heartbeats and
machine health are a member's self-reports (#348) and are labelled as such.

* **last heard**: the latest of the member's newest heartbeat, newest record, and
  ping answer, by when LTMS received it.
* **heartbeat interval**: LTMS isn't told a member's heartbeat setting, so it is
  read from the heartbeats themselves: the median gap (device time) between the
  newest ``INTERVAL_SAMPLE`` heartbeats. Unknown with fewer than two.
* **status**: ``on_time``; ``late`` when last heard is more than 1.5 × the
  interval ago; ``silent`` past 2 ×; ``unknown`` without an interval;
  ``never`` when nothing was ever heard.
* **health**: the newest ``health``-tagged self-report (a hub's machine health,
  #349) and the throttle alerts of the last 24 h; ``None`` when the member has
  never reported health.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Member, Record, RecordPayload, Swarm, SwarmMember
from server.services import ping, recall, specs
from server.services.profile import self_report_clause

INTERVAL_SAMPLE = 5
LATE_FACTOR = 1.5
SILENT_FACTOR = 2.0


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _aware(d: datetime | None) -> datetime | None:
    if d is not None and d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d


def interval_of(times: list[datetime]) -> float | None:
    """Median gap in seconds between the given heartbeat times (any order)."""
    ts = sorted(_aware(t) for t in times)
    gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:]) if b > a]
    return float(median(gaps)) if gaps else None


def status_of(last_heard: datetime | None, interval_s: float | None, now: datetime) -> str:
    if last_heard is None:
        return "never"
    if not interval_s:
        return "unknown"
    age = (now - _aware(last_heard)).total_seconds()
    if age > SILENT_FACTOR * interval_s:
        return "silent"
    if age > LATE_FACTOR * interval_s:
        return "late"
    return "on_time"


def kind_of(member: Member, role: str | None) -> str:
    """hub / robot / chat member / member, as the Health page shows it."""
    if role == "hub" or member.kind == "relay":
        return "hub"
    if member.kind == "robot":
        return "robot"
    if member.kind == "assistant":
        return "chat member"
    return "member"


async def _health(session: AsyncSession, member: Member, now: datetime) -> dict | None:
    tagged_health = recall._json_list_contains(session, Record.tags, "health")
    rec = await session.scalar(
        select(Record).where(Record.member_id == member.id, self_report_clause(session), tagged_health)
        .order_by(Record.received_at.desc(), Record.id.desc()).limit(1))
    if rec is None:
        return None
    body = await session.scalar(select(RecordPayload.body).where(RecordPayload.record_id == rec.id))
    throttles = await session.scalar(
        select(func.count()).select_from(Record).where(
            Record.member_id == member.id, Record.type == "event", Record.received_at >= now - timedelta(hours=24),
            tagged_health, recall._json_list_contains(session, Record.tags, "throttle"))) or 0
    return {"self_report": True, "ts": _iso(rec.ts), "received_at": _iso(rec.received_at),
            "summary": rec.summary, "payload": body if isinstance(body, dict) else None,
            "throttle_events_24h": int(throttles)}


async def _heartbeats(session: AsyncSession, member: Member) -> list[Record]:
    not_health = ~recall._json_list_contains(session, Record.tags, "health")
    return list(await session.scalars(
        select(Record).where(Record.member_id == member.id, self_report_clause(session), not_health)
        .order_by(Record.received_at.desc(), Record.id.desc()).limit(INTERVAL_SAMPLE)))


async def fleet(session: AsyncSession, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    memberships = {
        member_id: (swarm_name, role)
        for member_id, swarm_name, role in (await session.execute(
            select(SwarmMember.member_id, Swarm.name, SwarmMember.role)
            .join(Swarm, Swarm.id == SwarmMember.swarm_id)
            .where(SwarmMember.left_at.is_(None), Swarm.status == "active"))).all()
    }
    last_record = dict((await session.execute(
        select(Record.member_id, func.max(Record.received_at)).group_by(Record.member_id))).all())

    rows = []
    for m in await session.scalars(select(Member).where(Member.status == "active").order_by(Member.name)):
        swarm, role = memberships.get(m.id, (None, None))
        beats = await _heartbeats(session, m)
        last_beat = _aware(beats[0].received_at) if beats else None
        interval = interval_of([b.ts for b in beats])
        p = await ping.status(session, m.name)
        answered = _aware(datetime.fromisoformat(p["answered_at"])) if p.get("answered_at") else None
        heard = [t for t in (last_beat, _aware(last_record.get(m.id)), answered) if t is not None]
        last_heard = max(heard) if heard else None
        rows.append({
            "id": str(m.id), "name": m.name, "member_kind": m.kind, "kind": kind_of(m, role),
            "swarm": swarm, "role": role,
            "last_heard": _iso(last_heard), "last_heartbeat": _iso(last_beat),
            "last_record": _iso(_aware(last_record.get(m.id))), "last_ping_reply": _iso(answered),
            "heartbeat": {"self_report": True, "interval_s": interval, "interval_source": "observed" if interval else None,
                          "samples": len(beats)},
            "status": status_of(last_heard, interval, now),
            "health": await _health(session, m, now),
            "specs": {s: v["value"] for s, v in specs.resolve(m).items() if s in ("hardware", "models", "software")},
            "spec_summary": specs.summary_line(m),
            "ping": p,
        })
    return {"as_of": now.isoformat(), "late_factor": LATE_FACTOR, "silent_factor": SILENT_FACTOR, "members": rows}

