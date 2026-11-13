"""Where a member's AI runs: on board or off board (issue #185).

A robot whose AI runs off board (a body driven by a model on another machine)
is Hybrid; with on-board AI it is Physical AI. Three sources, always reported
with the value:

- ``admin``: an admin's override (``members.cognition_admin``)
- ``observed``: measured from the record trail (``cognition_observed``, #199)
- ``declared``: what the device reports (``cognition_declared``)

Resolution order: admin, then observed, then declared, then unknown.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Member, Record, RecordPayload
from server.models.member import COGNITION_LOCATIONS
from server.services.errors import ServiceError

MAX_MODELS = 4
MAX_TEXT = 64


def resolve(member: Member) -> dict:
    """``{"value", "source", "offboard_share"}``; value and source are None when unknown."""
    observed = member.cognition_observed if isinstance(member.cognition_observed, dict) else None
    share = observed.get("offboard_share") if observed else None
    if member.cognition_admin in COGNITION_LOCATIONS:
        return {"value": member.cognition_admin, "source": "admin", "offboard_share": share}
    if observed and observed.get("value") in COGNITION_LOCATIONS:
        return {"value": observed["value"], "source": "observed", "offboard_share": share}
    declared = member.cognition_declared if isinstance(member.cognition_declared, dict) else None
    if declared and declared.get("location") in COGNITION_LOCATIONS:
        return {"value": declared["location"], "source": "declared", "offboard_share": share}
    return {"value": None, "source": None, "offboard_share": share}


def _validate_declaration(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ServiceError(422, "bad_cognition", "expected an object with location and models")
    location = body.get("location")
    if location not in COGNITION_LOCATIONS:
        raise ServiceError(422, "bad_cognition", "location must be 'onboard' or 'offboard'")
    models = body.get("models") or []
    if not isinstance(models, list) or len(models) > MAX_MODELS:
        raise ServiceError(422, "bad_cognition", f"models must be a list of at most {MAX_MODELS}")
    clean = []
    for m in models:
        if not isinstance(m, dict) or not isinstance(m.get("name"), str) or not m["name"].strip():
            raise ServiceError(422, "bad_cognition", "each model needs a name")
        runs_on = m.get("runs_on")
        if runs_on is not None and not isinstance(runs_on, str):
            raise ServiceError(422, "bad_cognition", "runs_on must be a string or null")
        if len(m["name"]) > MAX_TEXT or (runs_on is not None and len(runs_on) > MAX_TEXT):
            raise ServiceError(422, "bad_cognition", f"name and runs_on are at most {MAX_TEXT} characters")
        clean.append({"name": m["name"].strip(), "runs_on": runs_on})
    return {"location": location, "models": clean}


async def declare(session: AsyncSession, member: Member, body: dict) -> dict:
    """The device reports where its AI runs. Audited when the location or models change."""
    decl = _validate_declaration(body)
    old = member.cognition_declared if isinstance(member.cognition_declared, dict) else None
    changed = old is None or old.get("location") != decl["location"] or old.get("models") != decl["models"]
    member.cognition_declared = {**decl, "reported_at": datetime.now(timezone.utc).isoformat()}
    # The models also go into the spec sheet (#332); cognition keeps owning the location.
    from server.services import specs

    await specs.report_models(session, member, decl["models"], decl["location"])
    if changed:
        session.add(AuditLog(
            actor=member.name, action="cognition_change", member_id=member.id,
            detail={"member": member.name, "source": "declared",
                    "old": old.get("location") if old else None, "new": decl["location"],
                    "models": decl["models"]},
        ))
    return resolve(member)


async def set_admin(session: AsyncSession, member: Member, value, *, admin: str) -> dict:
    """An admin's override; None clears it."""
    if value is not None and value not in COGNITION_LOCATIONS:
        raise ServiceError(422, "bad_cognition", "cognition must be 'onboard', 'offboard' or null")
    old = member.cognition_admin
    member.cognition_admin = value
    if old != value:
        session.add(AuditLog(
            actor=admin, action="cognition_change", member_id=member.id,
            detail={"member": member.name, "source": "admin", "old": old, "new": value},
        ))
    return resolve(member)


# --------------------------------------------------------------------------- #
# Observed (#199): where a robot's decisions are actually made
# --------------------------------------------------------------------------- #
MIN_EVENTS = 20
OBSERVE_BATCH = 500
OBSERVE_MAX = 20_000


def _names_other_agent(value, own: str) -> bool:
    """True when ``value`` ("agent:seq", or a list of them) names another member's record."""
    for v in value if isinstance(value, list) else [value]:
        if isinstance(v, str) and ":" in v:
            agent, _, seq = v.rpartition(":")
            if agent and agent != own and seq.isdigit():
                return True
    return False


async def observe(session: AsyncSession, member: Member, *, days: int = 7,
                  now: datetime | None = None) -> dict | None:
    """Measure where this robot's decisions are made over the last ``days`` days.

    Off-board: another member's action with ``payload.endpoint`` starting
    ``"<robot>/"``; the robot's action whose ``payload.directed_by`` names
    another member's record; the robot's decision whose ``payload.model`` is a
    declared model running somewhere other than the robot. On-board: the
    robot's other decisions and actions. Under MIN_EVENTS (20) events the
    value is null, with the evidence kept. Writes only ``cognition_observed``.
    Payloads are read in batches of 500 records, at most 20,000 per member.
    """
    if member.kind != "robot":
        return None
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    declared = member.cognition_declared if isinstance(member.cognition_declared, dict) else {}
    elsewhere = {m["name"] for m in declared.get("models") or []
                 if isinstance(m, dict) and m.get("runs_on") and m.get("runs_on") != member.name}
    prefix = f"{member.name}/"
    # Another member's actions are pre-filtered as text, then checked exactly.
    endpoint_like = f'%"endpoint": "{prefix}%'

    off = on = 0
    seen = 0
    after_id = 0
    while seen < OBSERVE_MAX:
        rows = (await session.execute(
            select(Record.id, Record.agent, Record.type, RecordPayload.body)
            .outerjoin(RecordPayload, RecordPayload.record_id == Record.id)
            .where(Record.id > after_id, Record.ts >= since, Record.type.in_(("decision", "action")),
                   (Record.member_id == member.id)
                   | ((Record.type == "action") & cast(RecordPayload.body, String).like(endpoint_like)))
            .order_by(Record.id).limit(OBSERVE_BATCH))).all()
        if not rows:
            break
        for rid, agent, rtype, body in rows:
            after_id = rid
            seen += 1
            body = body if isinstance(body, dict) else {}
            if agent != member.name:
                endpoint = body.get("endpoint")
                if rtype == "action" and isinstance(endpoint, str) and endpoint.startswith(prefix):
                    off += 1
                continue
            if rtype == "action" and _names_other_agent(body.get("directed_by"), member.name):
                off += 1
            elif rtype == "decision" and body.get("model") in elsewhere:
                off += 1
            else:
                on += 1
        if len(rows) < OBSERVE_BATCH:
            break

    total = off + on
    share = off / total if total >= MIN_EVENTS else None
    value = None if share is None else ("offboard" if share >= 0.5 else "onboard")
    old = member.cognition_observed if isinstance(member.cognition_observed, dict) else {}
    member.cognition_observed = {"value": value, "offboard_share": share, "evidence": {"off": off, "on": on},
                                 "days": days, "observed_at": now.isoformat()}
    if old.get("value") != value:
        session.add(AuditLog(actor="system:cognition", action="cognition_change", member_id=member.id,
                             detail={"member": member.name, "source": "observed",
                                     "old": old.get("value"), "new": value, "offboard_share": share}))
    return member.cognition_observed


async def observe_all(session: AsyncSession, *, days: int = 7) -> dict:
    """Nightly: observe every active robot."""
    robots = list(await session.scalars(select(Member).where(Member.kind == "robot", Member.status == "active")))
    for m in robots:
        await observe(session, m, days=days)
    return {"observed": len(robots)}
