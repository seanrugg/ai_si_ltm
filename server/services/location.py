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

Members that only reach LTMS through a hub (#346) take the network estimate
of the hub that relayed their newest record. A member with nothing else
inherits its swarm's estimate (the hub's, else the first member's) until an
admin declares a swarm home. Every member with no position gets an
``unplaced`` reason, and ``geo`` says whether network estimates work at all.
"""
from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import AuditLog, DeclaredLocation, Member, MemberAddress, Record, RecordProvenance, Swarm, SwarmMember
from server.models.location import LOCATION_SUBJECTS
from server.services import recall
from server.services.errors import ServiceError

log = logging.getLogger("ltms.location")

GPS_SCAN = 200          # newest sensed records looked at per member for a GPS fix
# The LTMS server's own location: a declared "member" entry with this name,
# which needs no member row (the globe draws the server as a site, #195).
SERVER_NAME = "ltms-server"
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
    if subject_type == "member" and name == SERVER_NAME:
        return
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


def geo_status(cfg) -> dict:
    """Whether network estimates can work, and if not, why (#346)."""
    import os

    path = getattr(cfg, "geoip_db_path", None)
    if not path:
        return {"enabled": False, "path": None,
                "reason": "LTMS_GEOIP_DB isn't set: no city database, so no network estimates"}
    try:
        import maxminddb  # noqa: F401
    except ImportError:
        return {"enabled": False, "path": path,
                "reason": 'the geo extra isn\'t installed (pip install "ltms[geo]")'}
    if not os.path.isfile(path):
        return {"enabled": False, "path": path, "reason": f"no file at {path}"}
    return {"enabled": True, "path": path, "reason": None}


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
async def _newest_seen(session, member: Member) -> MemberAddress | None:
    return await session.scalar(
        select(MemberAddress).where(MemberAddress.member_id == member.id, MemberAddress.source == "seen")
        .order_by(MemberAddress.last_seen.desc(), MemberAddress.id.desc()).limit(1))


async def _network_estimate(session, db_path: str, member: Member, via: str | None = None) -> dict | None:
    seen = await _newest_seen(session, member)
    if seen is None or not _public(seen.ip):
        return None
    hit = lookup_city(db_path, seen.ip)
    if not hit:
        return None
    extra = {"via_hub": via} if via else {}
    return _entry("network", hit.get("label"), None, hit.get("lat"), hit.get("lon"),
                  hit.get("accuracy_km") or DEFAULT_ACCURACY_KM, **extra)


async def _relaying_hub(session, member: Member) -> Member | None:
    """The member that relayed this member's newest relayed record, if any."""
    hub_id = await session.scalar(
        select(RecordProvenance.relay_member_id)
        .join(Record, Record.id == RecordProvenance.record_id)
        .where(Record.member_id == member.id, RecordProvenance.relay_member_id.is_not(None),
               RecordProvenance.relay_member_id != member.id)
        .order_by(RecordProvenance.received_at.desc()).limit(1))
    return await session.get(Member, hub_id) if hub_id else None


async def _unplaced_reason(session, row: dict, geo: dict, declared: dict) -> str:
    """Why a member has no position on the globe (#346), in one sentence."""
    no_home = (f"swarm {row['_swarm']} has no declared home" if row["_swarm"]
               else "it isn't in a swarm with a declared home")
    member = await session.scalar(select(Member).where(Member.name == row["name"]))
    seen = await _newest_seen(session, member) if member is not None else None
    if seen is not None and not _public(seen.ip):
        return (f"Only a local network address ({seen.ip}) has been seen, which can't be located, "
                f"and {no_home}. Declare a location for the member or its swarm.")
    if not geo["enabled"]:
        return f"No GPS or declared location, {no_home}, and network estimates are off: {geo['reason']}."
    if seen is None:
        return f"It has never connected to LTMS directly, and {no_home}."
    return f"Its address ({seen.ip}) isn't in the city database, and {no_home}."


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
    geo = geo_status(cfg)

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
        net = None
        if db_path:  # geo_status only explains; a lookup that can't work just finds nothing
            net = await _network_estimate(session, db_path, m)
            if net is None:
                hub = await _relaying_hub(session, m)
                if hub is not None:
                    net = await _network_estimate(session, db_path, hub, via=hub.name)
            if net is not None:
                entries.append(net)
        out.append({"name": m.name, "best": entries[0] if entries else None, "all": entries,
                    "_swarm": swarm_of.get(m.id)})

    # A swarm with no declared home: members with nothing else inherit its network estimate,
    # from its hub if the hub has one, else from the first member that does.
    estimate: dict[str, tuple[dict, str]] = {}
    if db_path:
        for sw in {r["_swarm"] for r in out if r["_swarm"] and not r["all"] and ("swarm", r["_swarm"]) not in declared}:
            ms = list(await session.scalars(
                select(Member).join(SwarmMember, SwarmMember.member_id == Member.id).join(
                    Swarm, Swarm.id == SwarmMember.swarm_id)
                .where(Swarm.name == sw, SwarmMember.left_at.is_(None))
                .order_by((SwarmMember.role != "hub"), Member.name)))
            for m in ms:
                net = await _network_estimate(session, db_path, m)
                if net is not None:
                    estimate[sw] = (net, m.name)
                    break
    for row in out:
        sw = row["_swarm"]
        if not row["all"] and sw in estimate:
            net, from_member = estimate[sw]
            inherited = {**net, "inherited_from": f"swarm {sw} (network estimate from {from_member})"}
            row["all"].append(inherited)
            row["best"] = inherited
        if row["best"] is None or row["best"].get("lat") is None:
            row["unplaced"] = await _unplaced_reason(session, row, geo, declared)
        row.pop("_swarm")

    server = declared.get(("member", SERVER_NAME))
    # Declared swarm locations, for the dashboard's location forms (#228).
    swarms = {name: _entry("declared", d.label, d.room, d.lat, d.lon)
              for (kind, name), d in sorted(declared.items()) if kind == "swarm"}
    return {"members": out, "swarms": swarms, "geo": geo,
            "server": _entry("declared", server.label, server.room, server.lat, server.lon) if server else None}
