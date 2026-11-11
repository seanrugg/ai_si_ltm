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

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, Member
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
