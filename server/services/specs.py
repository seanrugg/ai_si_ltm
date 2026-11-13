"""Member spec sheet (#332): hardware, language models and software, as the device
reports them, with an admin's per-field overrides. Member metadata, not a record.

``resolve()`` merges field by field (admin over declared) and labels each section
with its source. A report that changes anything is audited (``specs_change``)
with what changed; an identical re-report writes nothing.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import func, select

from ltms_contract import canonicalize
from server.models import AuditLog, Member
from server.services.errors import ServiceError

MAX_STR = 64
MAX_LIST = 16
MAX_NOTES = 500
MAX_BYTES = 8 * 1024
WHERE = ("onboard", "offboard")

HARDWARE = {"board": str, "cpu": str, "cores": int, "ram_mib": int, "storage_gib": int,
            "accelerators": list, "sensors": list, "actuators": list, "power": str}
SOFTWARE = {"os": str, "harness": str, "harness_version": str}
MODEL = {"name": str, "params": str, "quant": str, "runtime": str, "where": str, "role": str}
SECTIONS = ("hardware", "models", "software", "notes")


def _bad(msg: str):
    raise ServiceError(422, "bad_specs", msg)


def _str(v, where: str, limit: int = MAX_STR) -> str:
    if not isinstance(v, str):
        _bad(f"{where} must be text")
    if len(v) > limit:
        _bad(f"{where} is at most {limit} characters")
    return v.strip()


def _fields(body, schema: dict, where: str, *, allow_null: bool = False) -> dict:
    if not isinstance(body, dict):
        _bad(f"{where} must be an object")
    out = {}
    for k, v in body.items():
        if k not in schema:
            _bad(f"unknown field {where}.{k}")
        if v is None and allow_null:
            out[k] = None
            continue
        kind = schema[k]
        if kind is str:
            out[k] = _str(v, f"{where}.{k}")
            if k == "where" and out[k] not in WHERE:
                _bad(f"{where}.where must be onboard or offboard")
        elif kind is int:
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                _bad(f"{where}.{k} must be a whole number ≥ 0")
            out[k] = v
        else:
            if not isinstance(v, list) or len(v) > MAX_LIST:
                _bad(f"{where}.{k} must be a list of at most {MAX_LIST}")
            out[k] = [_str(x, f"{where}.{k}[]") for x in v]
    return out


def _models(v) -> list[dict]:
    if not isinstance(v, list) or len(v) > MAX_LIST:
        _bad(f"models must be a list of at most {MAX_LIST}")
    out = []
    for m in v:
        m = _fields(m, MODEL, "models[]")
        if not m.get("name"):
            _bad("each model needs a name")
        out.append(m)
    return out


def validate(body, *, patch: bool = False) -> dict:
    """The spec sheet in canonical form. ``patch``: fields may be null (clear an override)."""
    if not isinstance(body, dict):
        _bad("expected an object")
    try:
        size = len(canonicalize(body))
    except Exception:  # noqa: BLE001 - not JSON-shaped
        _bad("the spec sheet must be plain JSON")
    if size > MAX_BYTES:
        raise ServiceError(413, "too_large", f"a spec sheet is at most {MAX_BYTES} bytes")
    out = {}
    for k, v in body.items():
        if k not in SECTIONS:
            _bad(f"unknown section {k!r}")
        if v is None and patch:
            out[k] = None
        elif k == "hardware":
            out[k] = _fields(v, HARDWARE, "hardware", allow_null=patch)
        elif k == "software":
            out[k] = _fields(v, SOFTWARE, "software", allow_null=patch)
        elif k == "models":
            out[k] = _models(v)
        else:
            out[k] = _str(v, "notes", MAX_NOTES)
    return out


def _changed_sections(old: dict, new: dict) -> list[str]:
    return [s for s in SECTIONS if (old or {}).get(s) != (new or {}).get(s)]


async def report(session, member: Member, body, *, actor: str | None = None) -> dict:
    """The device reports its spec sheet (replaces what it reported before)."""
    new = validate(body)
    old = member.specs_declared or {}
    changed = _changed_sections(old, new)
    member.specs_reported_at = datetime.now(timezone.utc)
    if changed:
        member.specs_declared = new
        session.add(AuditLog(actor=actor or member.name, action="specs_change", member_id=member.id,
                             detail={"member": member.name, "source": "declared", "sections": changed,
                                     "old": {s: old.get(s) for s in changed}, "new": {s: new.get(s) for s in changed}}))
    return resolve(member)


async def report_models(session, member: Member, models: list[dict], location: str) -> None:
    """Cognition's models (#185) flow into the spec sheet: name and where it runs."""
    sheet = copy.deepcopy(member.specs_declared or {})
    old = sheet.get("models") or []
    by_name = {m.get("name"): m for m in old}
    new = []
    for m in models:
        entry = dict(by_name.get(m["name"], {"name": m["name"][:MAX_STR]}))
        entry["where"] = location if location in WHERE else entry.get("where")
        if entry.get("where") is None:
            entry.pop("where", None)
        if m.get("runs_on") and not entry.get("runtime"):
            entry["runtime"] = m["runs_on"][:MAX_STR]
        new.append(entry)
    if new != old:
        sheet["models"] = new
        await report(session, member, sheet, actor=member.name)


async def admin_patch(session, member: Member, body, *, admin: str) -> dict:
    """Set (value) or clear (null) override fields. Never edits what the device reported."""
    patch = validate(body, patch=True)
    over = copy.deepcopy(member.specs_admin or {})
    before = copy.deepcopy(over)
    for section, value in patch.items():
        if value is None:
            over.pop(section, None)
        elif section in ("hardware", "software"):
            cur = over.get(section, {})
            for k, v in value.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
            if cur:
                over[section] = cur
            else:
                over.pop(section, None)
        else:
            over[section] = value
    member.specs_admin = over or None
    changed = _changed_sections(before, over)
    if changed:
        session.add(AuditLog(actor=admin, action="specs_change", member_id=member.id,
                             detail={"member": member.name, "source": "admin", "sections": changed,
                                     "old": {s: before.get(s) for s in changed}, "new": {s: over.get(s) for s in changed}}))
    return resolve(member)


def resolve(member: Member) -> dict:
    declared, over = member.specs_declared or {}, member.specs_admin or {}
    at = member.specs_reported_at.isoformat() if member.specs_reported_at else None
    out = {}
    for s in SECTIONS:
        d, a = declared.get(s), over.get(s)
        if s in ("hardware", "software"):
            merged = {**(d or {}), **(a or {})}
            admin_fields = sorted((a or {}).keys())
            value = merged or None
            source = "admin" if admin_fields and set(merged) == set(admin_fields) else (
                "mixed" if admin_fields else ("declared" if d else None))
        else:
            value = a if a is not None else d
            admin_fields = [s] if a is not None else []
            source = "admin" if a is not None else ("declared" if d is not None else None)
        out[s] = {"value": value, "source": source, "admin_fields": admin_fields,
                  "declared": d, "reported_at": at if d is not None else None}
    return out


async def history(session, member: Member, *, limit: int = 20, offset: int = 0) -> dict:
    rows = list(await session.scalars(
        select(AuditLog).where(AuditLog.action == "specs_change", AuditLog.member_id == member.id)
        .order_by(AuditLog.id.desc()).limit(limit).offset(offset)))
    total = await session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "specs_change", AuditLog.member_id == member.id)) or 0
    return {"total": total, "events": [{"ts": r.ts.isoformat() if getattr(r, "ts", None) else None, "actor": r.actor,
                                        **{k: (r.detail or {}).get(k) for k in ("source", "sections", "old", "new")}}
                                       for r in rows]}


def summary_line(member: Member) -> str:
    """One line from the spec sheet for a roster (#334), e.g. "Jetson Orin Nano 8 GB · qwen3:4b on board"."""
    r = resolve(member)
    hw = r["hardware"]["value"] or {}
    models = r["models"]["value"] or []
    parts = []
    board = " ".join(p for p in (hw.get("board"), f"{round(hw['ram_mib'] / 1024)} GB" if hw.get("ram_mib") else None) if p)
    if board:
        parts.append(board)
    if models:
        m = models[0]
        where = {"onboard": "on board", "offboard": "off board"}.get(m.get("where"), "")
        parts.append(" ".join(p for p in (m.get("name"), where) if p))
    return " · ".join(parts)
