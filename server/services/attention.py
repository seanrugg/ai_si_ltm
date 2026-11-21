"""Overview's "Needs attention" (#380): what's waiting on an admin, in one list.

Each item: ``key`` (stable, for snoozing), ``kind``, ``severity`` (``action`` or
``info``), ``title`` (one line), ``count`` or ``subject``, ``link`` (where it's
resolved) and ``since`` (how long it's been waiting, when known). Sorted by
severity, then oldest first.

Counts that the nav shows as badges come from :mod:`nav_status` itself, so the
badges and Overview always agree. Silent members and hub health come from
:mod:`fleet_health` (#381). Integrity reuses the last full check for up to
``INTEGRITY_MAX_AGE``, because a full check reads every chain.

Snoozing hides an item from one admin for ``SNOOZE`` (24 h); it doesn't resolve
it, and other admins still see it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AttentionSnooze, Dataset, DatasetVersion, Directive, Member
from server.services import directives, fleet_health, integrity, nav_status
from server.services.errors import ServiceError

SNOOZE = timedelta(hours=24)
DEFAULT_INTERVAL_S = 24 * 3600   # a member whose heartbeat interval isn't known yet
NOT_RECEIVED_GRACE = timedelta(hours=1)  # give hubs time to pull a new directive
INTEGRITY_MAX_AGE = timedelta(hours=1)
SEVERITY_ORDER = {"action": 0, "info": 1}
UP = {"active", "up", "running", "ok"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    if d is not None and d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d


def _iso(d: datetime | None) -> str | None:
    return _aware(d).isoformat() if d else None


def _item(key, kind, severity, title, link, *, count=None, subject=None, since=None, **extra) -> dict:
    return {"key": key, "kind": kind, "severity": severity, "title": title, "link": link,
            "count": count, "subject": subject, "since": _iso(since) if isinstance(since, datetime) else since,
            **extra}


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


# --- health and silence (#381's fleet rows) ------------------------------------

def health_problems(health: dict | None) -> list[str]:
    """What's wrong in a hub's latest health self-report, in words. Empty when fine."""
    if not health:
        return []
    p = health.get("payload") or {}
    out = []
    n = health.get("throttle_events_24h") or 0
    if n:
        out.append(f"{plural(n, 'throttle event')} in 24 h")
    elif p.get("throttled"):
        out.append("throttling")
    for name, state in sorted((p.get("services") or {}).items()):
        if str(state).lower() not in UP:
            out.append(f"{name} {state}")
    return out


def is_silent(row: dict, now: datetime) -> bool:
    """No heartbeat or record for over 2× the member's interval (24 h when unknown)."""
    if not row.get("last_heard"):
        return False  # never heard: an enrollment matter, not a silence
    interval = (row.get("heartbeat") or {}).get("interval_s") or DEFAULT_INTERVAL_S
    age = (now - datetime.fromisoformat(row["last_heard"])).total_seconds()
    return age > fleet_health.SILENT_FACTOR * interval


# --- integrity ------------------------------------------------------------------

async def integrity_summary(session: AsyncSession, state, now: datetime) -> dict:
    """The last chain check, refreshed (incrementally, #439) when older than INTEGRITY_MAX_AGE."""
    cached = getattr(state, "integrity_summary", None)
    if cached is None or now - cached["checked_at"] > INTEGRITY_MAX_AGE:
        cached = remember_integrity(state, await integrity.check_all(session), now)
    return cached


def remember_integrity(state, reports: list[dict], now: datetime | None = None) -> dict:
    """Keep a check's result for Overview (also called by the Integrity page and a full run)."""
    summary = {"checked_at": now or _now(),
               "broken": sorted(r["member"] for r in reports if r.get("status") == "broken")}
    state.integrity_summary = summary
    return summary


# --- items ------------------------------------------------------------------------

async def _directive_items(session: AsyncSession, now: datetime, declines: int) -> list[dict]:
    items = []
    live = [d for d in await session.scalars(select(Directive).order_by(Directive.issued_at))
            if directives.status_of(d, now) == "open"]
    acks = await directives.acknowledgements(session, live)
    if declines:
        reasons = [f"{x['member']}: {x['reason']}" if x.get("reason") else x["member"]
                   for d in live for x in acks.get(str(d.id), {}).get("declines", [])]
        items.append(_item("directive_declines", "directive_declines", "action",
                           f"{plural(declines, 'decline')} of a directive since you last looked",
                           "/directives", count=declines, reasons=reasons[:5]))
    for d in live:
        a = acks.get(str(d.id)) or {}
        missing = (a.get("meant_for") or 0) - (a.get("received") or 0)
        if missing > 0 and now - _aware(d.issued_at) > NOT_RECEIVED_GRACE:
            items.append(_item(f"directive_unreceived:{d.id}", "directive_unreceived", "info",
                               f"Directive not received by {missing} of {a['meant_for']} members: {d.text[:80]}",
                               "/directives", count=missing, subject=str(d.id), since=d.issued_at))
    return items


async def items(session: AsyncSession, state, cfg, *, directives_seen: datetime | None = None,
                now: datetime | None = None) -> tuple[list[dict], dict]:
    """Every current item, unsorted and unsnoozed, plus the nav badge counts used."""
    now = now or _now()
    counts = await nav_status.status(session, cfg, directives_seen=directives_seen)
    out: list[dict] = []

    if counts["members_pending"]:
        oldest = await session.scalar(select(func.min(Member.created_at)).where(Member.status == "pending"))
        out.append(_item("enrollments", "enrollments", "action",
                         f"{plural(counts['members_pending'], 'enrollment')} waiting for approval",
                         "/members", count=counts["members_pending"], since=oldest))
    if counts["charter_overdue"]:
        out.append(_item("charter_overdue", "charter_overdue", "action",
                         f"{plural(counts['charter_overdue'], 'member')} overdue on the current charter; their writes are paused",
                         "/charter", count=counts["charter_overdue"]))
    out += await _directive_items(session, now, counts["directive_declines"])

    fleet = await fleet_health.fleet(session, now=now)
    for row in fleet["members"]:
        name = row["name"]
        profile = f"/members/{name}/profile?tab=senses"
        if is_silent(row, now):
            interval = (row.get("heartbeat") or {}).get("interval_s")
            basis = "its heartbeat interval" if interval else "24 h (no heartbeat interval yet)"
            out.append(_item(f"silent:{name}", "silent", "action",
                             f"{name} is silent: nothing heard for over 2× {basis}",
                             profile, subject=name, since=row["last_heard"], ping=True))
        problems = health_problems(row.get("health"))
        if problems:
            out.append(_item(f"health:{name}", "health", "action" if row["kind"] == "hub" else "info",
                             f"{name} health: {', '.join(problems)}",
                             profile, subject=name, since=(row.get("health") or {}).get("received_at")))

    if counts["lrs_failing"]:
        out.append(_item("lrs", "lrs", "action",
                         f"{plural(counts['lrs_failing'], 'LRS endpoint')} failing or paused",
                         "/lrs", count=counts["lrs_failing"]))

    summary = await integrity_summary(session, state, now)
    if summary["broken"]:
        names = summary["broken"]
        out.append(_item("integrity", "integrity", "action",
                         f"Chain break or verification failure: {', '.join(names[:5])}"
                         + (f" and {len(names) - 5} more" if len(names) > 5 else ""),
                         "/integrity", count=len(names), checked_at=_iso(summary["checked_at"])))

    pending, oldest = (await session.execute(
        select(func.count(), func.min(DatasetVersion.created_at))
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(DatasetVersion.status == "pending", Dataset.kind != "charter"))).one()
    if pending:
        out.append(_item("dataset_versions", "dataset_versions", "info",
                         f"{plural(pending, 'dataset version')} waiting for approval",
                         "/datasets", count=int(pending), since=oldest))
    return out, counts


def _sort_key(item: dict):
    return (SEVERITY_ORDER.get(item["severity"], 9), item["since"] is None, item["since"] or "", item["key"])


async def snoozed(session: AsyncSession, admin_id, now: datetime) -> dict[str, datetime]:
    rows = await session.execute(select(AttentionSnooze.item_key, AttentionSnooze.until)
                                 .where(AttentionSnooze.admin_id == admin_id, AttentionSnooze.until > now))
    return {k: _aware(u) for k, u in rows.all()}


async def view(session: AsyncSession, state, cfg, admin_id, *, directives_seen: datetime | None = None,
               now: datetime | None = None) -> dict:
    now = now or _now()
    all_items, counts = await items(session, state, cfg, directives_seen=directives_seen, now=now)
    hidden = await snoozed(session, admin_id, now)
    shown = sorted((i for i in all_items if i["key"] not in hidden), key=_sort_key)
    return {"checked_at": now.isoformat(), "items": shown,
            "snoozed": [{"key": i["key"], "title": i["title"], "until": hidden[i["key"]].isoformat()}
                        for i in all_items if i["key"] in hidden],
            "counts": {k: counts[k] for k in ("members_pending", "charter_overdue", "directive_declines", "lrs_failing")}}


async def snooze(session: AsyncSession, admin_id, key: str, *, now: datetime | None = None) -> dict:
    key = (key or "").strip()
    if not key or len(key) > 200:
        raise ServiceError(422, "bad_key", "An item key of 1 to 200 characters is required.")
    now = now or _now()
    until = now + SNOOZE
    row = await session.scalar(select(AttentionSnooze).where(AttentionSnooze.admin_id == admin_id,
                                                             AttentionSnooze.item_key == key))
    if row is None:
        session.add(AttentionSnooze(admin_id=admin_id, item_key=key, until=until))
    else:
        row.until = until
    await session.execute(delete(AttentionSnooze).where(AttentionSnooze.admin_id == admin_id,
                                                        AttentionSnooze.until <= now))
    await session.flush()
    return {"key": key, "until": until.isoformat()}


async def unsnooze(session: AsyncSession, admin_id, key: str) -> dict:
    await session.execute(delete(AttentionSnooze).where(AttentionSnooze.admin_id == admin_id,
                                                        AttentionSnooze.item_key == key))
    return {"key": key, "until": None}
