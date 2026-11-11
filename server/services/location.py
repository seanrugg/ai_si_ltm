"""Where a member is, from three sources (issue #191). Works on a closed network.

- ``gps``: the newest sensed record whose payload has ``modality == "gps"``
  (sample ``v`` = ``{"lat", "lon"}``, optional ``accuracy_m``). High precision.
- ``declared``: set by an admin on the member, or inherited from its swarm.
- ``network``: the member's newest public "seen" address (#189), looked up in
  an optional **offline** city database (``LTMS_GEOIP_DB``, needs the ``geo``
  extra). City at best. Private, loopback and link-local addresses are never
  looked up, and nothing is ever looked up online.

``best`` is the first of gps, declared (member, then swarm), network.
Admin only; locations are never sent to an LRS.
"""
from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, DeclaredLocation, Member, MemberAddress, Record, Swarm, SwarmMember
from server.models.location import LOCATION_SUBJECTS
from server.services import recall
from server.services.errors import ServiceError

log = logging.getLogger("ltms.location")

GPS_SCAN = 200          # newest sensed records looked at per member for a GPS fix
DEFAULT_ACCURACY_KM = 50


def _entry(source: str, label: str | None, room=None, lat=None, lon=None, accuracy_km=None, **extra) -> dict:
    return {"source": source, "label": label, "room": room, "lat": lat, "lon": lon,
            "accuracy_km": accuracy_km, **extra}


# --------------------------------------------------------------------------- #
# Declared
# --------------------------------------------------------------------------- #
def _validate(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ServiceError(422, "bad_location", "expected {label, room, lat, lon}")
    label = body.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 80:
        raise ServiceError(422, "bad_location", "label is required, at most 80 characters")
    room = body.get("room")
    if room is not None and (not isinstance(room, str) or len(room) > 40):
        raise ServiceError(422, "bad_location", "room is at most 40 characters")
    lat, lon = body.get("lat"), body.get("lon")
    if (lat is None) != (lon is None):
        raise ServiceError(422, "bad_location", "give both lat and lon, or neither")
    for name, v, limit in (("lat", lat, 90), ("lon", lon, 180)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not -limit <= v <= limit):
            raise ServiceError(422, "bad_location", f"{name} must be a number from -{limit} to {limit}")
    return {"label": label.strip(), "room": (room or "").strip() or None,
            "lat": float(lat) if lat is not None else None, "lon": float(lon) if lon is not None else None}


def _declared_dict(d: DeclaredLocation | None) -> dict | None:
    return None if d is None else {"label": d.label, "room": d.room, "lat": d.lat, "lon": d.lon}


async def _subject_exists(session: AsyncSession, subject_type: str, name: str) -> None:
    if subject_type not in LOCATION_SUBJECTS:
        raise ServiceError(404, "not_found", "locations are for a member or a swarm")
    model = Member if subject_type == "member" else Swarm
    if await session.scalar(select(model.id).where(model.name == name)) is None:
        raise ServiceError(404, "not_found", f"no {subject_type} {name!r}")


async def _get_declared(session, subject_type, name) -> DeclaredLocation | None:
    return await session.scalar(select(DeclaredLocation).where(
        DeclaredLocation.subject_type == subject_type, DeclaredLocation.subject_name == name))


async def set_declared(session: AsyncSession, subject_type: str, name: str, body: dict, *, admin: str) -> dict:
    await _subject_exists(session, subject_type, name)
    new = _validate(body)
    row = await _get_declared(session, subject_type, name)
    old = _declared_dict(row)
    if row is None:
        row = DeclaredLocation(subject_type=subject_type, subject_name=name, updated_by=admin, **new)
        session.add(row)
    else:
        for k, v in new.items():
            setattr(row, k, v)
        row.updated_by, row.updated_at = admin, datetime.now(timezone.utc)
    if old != new:
        session.add(AuditLog(actor=admin, action="location_change",
                             detail={"subject_type": subject_type, "name": name, "old": old, "new": new}))
    return new


async def delete_declared(session: AsyncSession, subject_type: str, name: str, *, admin: str) -> bool:
    await _subject_exists(session, subject_type, name)
    row = await _get_declared(session, subject_type, name)
    if row is None:
        return False
    session.add(AuditLog(actor=admin, action="location_change",
                         detail={"subject_type": subject_type, "name": name, "old": _declared_dict(row), "new": None}))
    await session.delete(row)
    return True


# --------------------------------------------------------------------------- #
# Network estimate (offline only)
# --------------------------------------------------------------------------- #
def lookup_city(db_path: str, ip: str) -> dict | None:
    """Look ``ip`` up in an offline MaxMind-format city database, or None.

    Tests replace this function. Returns None when the database or the
    ``maxminddb`` package is missing.
    """
    try:
        import maxminddb  # optional: pip install "ltms[geo]"
    except ImportError:
        return None
    try:
        with maxminddb.open_database(db_path) as reader:
            hit = reader.get(ip)
    except (OSError, ValueError) as exc:
        log.warning("geoip lookup unavailable: %s", exc)
        return None
    if not isinstance(hit, dict) or "location" not in hit:
        return None
    loc = hit.get("location") or {}
    city = ((hit.get("city") or {}).get("names") or {}).get("en")
    country = ((hit.get("country") or {}).get("names") or {}).get("en")
    return {"label": ", ".join(p for p in (city, country) if p) or None,
            "lat": loc.get("latitude"), "lon": loc.get("longitude"),
            "accuracy_km": loc.get("accuracy_radius") or DEFAULT_ACCURACY_KM}


def _public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                or a.is_reserved or a.is_unspecified)


# --------------------------------------------------------------------------- #
# GPS
# --------------------------------------------------------------------------- #
def _gps_fix(payload) -> dict | None:
    if not isinstance(payload, dict) or payload.get("modality") != "gps":
        return None
    for sample in reversed(payload.get("samples") or []):
        v = sample.get("v") if isinstance(sample, dict) else None
        if isinstance(v, dict) and isinstance(v.get("lat"), (int, float)) and isinstance(v.get("lon"), (int, float)):
            acc = v.get("accuracy_m", payload.get("accuracy_m"))
            return {"lat": float(v["lat"]), "lon": float(v["lon"]),
                    "accuracy_km": acc / 1000 if isinstance(acc, (int, float)) else None}
    return None


async def _gps(session: AsyncSession, member: Member) -> dict | None:
    rows = list(await session.scalars(
        select(Record).where(Record.member_id == member.id, Record.type == "perception",
                             recall._json_list_contains(session, Record.tags, "telemetry"))
        .order_by(Record.ts.desc(), Record.id.desc()).limit(GPS_SCAN)))
    payloads = await recall.fetch_payloads(session, rows)
    for r in rows:
        row = payloads.get(r.id)
        fix = _gps_fix(row.body if row is not None else None)
        if fix:
            return _entry("gps", "GPS fix", ts=r.ts.isoformat(), **fix)
    return None


# --------------------------------------------------------------------------- #
# All sources for each member
# --------------------------------------------------------------------------- #
async def describe(session: AsyncSession, cfg, *, swarm: str | None = None, member: str | None = None) -> dict:
    stmt = select(Member).where(Member.status != "rejected").order_by(Member.name)
    if member:
        stmt = stmt.where(Member.name == member)
    if swarm:
        s = await session.scalar(select(Swarm).where(Swarm.name == swarm))
        if s is None:
            raise ServiceError(404, "not_found", f"no swarm {swarm!r}")
        stmt = stmt.where(Member.id.in_(select(SwarmMember.member_id).where(
            SwarmMember.swarm_id == s.id, SwarmMember.left_at.is_(None))))
    members = list(await session.scalars(stmt))
    if member and not members:
        raise ServiceError(404, "not_found", f"no member {member!r}")

    declared = {(d.subject_type, d.subject_name): d for d in await session.scalars(select(DeclaredLocation))}
    swarm_of = dict((await session.execute(
        select(SwarmMember.member_id, Swarm.name).join(Swarm, Swarm.id == SwarmMember.swarm_id)
        .where(SwarmMember.left_at.is_(None)))).all())
    db_path = getattr(cfg, "geoip_db_path", None)

    out = []
    for m in members:
        entries = []
        gps = await _gps(session, m)
        if gps:
            entries.append(gps)
        for subject_type, name in (("member", m.name), ("swarm", swarm_of.get(m.id))):
            d = declared.get((subject_type, name)) if name else None
            if d is not None:
                entries.append(_entry("declared", d.label, d.room, d.lat, d.lon, None, inherited_from=(
                    f"swarm {name}" if subject_type == "swarm" else None)))
        if db_path:
            seen = await session.scalar(
                select(MemberAddress).where(MemberAddress.member_id == m.id, MemberAddress.source == "seen")
                .order_by(MemberAddress.last_seen.desc(), MemberAddress.id.desc()).limit(1))
            if seen is not None and _public(seen.ip):
                hit = lookup_city(db_path, seen.ip)
                if hit:
                    entries.append(_entry("network", hit.get("label"), None, hit.get("lat"), hit.get("lon"),
                                          hit.get("accuracy_km") or DEFAULT_ACCURACY_KM))
        out.append({"name": m.name, "best": entries[0] if entries else None, "all": entries})
    return {"members": out}
