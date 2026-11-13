"""Member locations: gps, declared, network (issue #191)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from server.models import AuditLog
from server.services import location
from server.tests.test_swarms import Home, sign


@pytest.fixture
def ip(monkeypatch):
    current = {"ip": "192.168.1.20"}
    for mod in ("server.api.public", "server.api.member"):
        monkeypatch.setattr(f"{mod}.client_ip", lambda request: current["ip"])
    return current


def _loc(admin, name):
    r = admin.get("/api/admin/locations", params={"member": name})
    assert r.status_code == 200, r.text
    (m,) = r.json()["members"]
    return m


def _gps(client, lat, lon, accuracy_m=None):
    v = {"lat": lat, "lon": lon, **({"accuracy_m": accuracy_m} if accuracy_m else {})}
    client.append("perception", summary="fix", tags=["telemetry"],
                  payload={"kind": "sensor", "sensor": "gps", "modality": "gps", "samples": [{"t": 0, "v": v}]})
    client.flush()


def test_member_declared_beats_swarm_and_gps_beats_both(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    z3 = mesh.member("z3-r0")
    home.add(z3)
    first = _loc(admin, "z3-r0")
    assert first["best"] is None and first["all"] == [] and "no declared home" in first["unplaced"]

    assert admin.put("/api/admin/locations/swarm/echo-base", json={"label": "Site A"}).status_code == 200
    assert admin.get("/api/admin/locations").json()["swarms"]["echo-base"]["label"] == "Site A"
    best = _loc(admin, "z3-r0")["best"]
    assert (best["source"], best["label"], best["inherited_from"]) == ("declared", "Site A", "swarm echo-base")

    r = admin.put("/api/admin/locations/member/z3-r0", json={"label": "Lab", "room": "B12", "lat": 10.5, "lon": -20})
    assert r.status_code == 200, r.text
    m = _loc(admin, "z3-r0")
    assert (m["best"]["label"], m["best"]["room"], m["best"]["inherited_from"]) == ("Lab", "B12", None)
    assert [e["label"] for e in m["all"]] == ["Lab", "Site A"]

    _gps(z3, 1.25, 2.5, accuracy_m=8)
    best = _loc(admin, "z3-r0")["best"]
    assert (best["source"], best["lat"], best["lon"], best["accuracy_km"]) == ("gps", 1.25, 2.5, 0.008)

    assert admin.delete("/api/admin/locations/member/z3-r0").json() == {"deleted": True}
    assert [e["source"] for e in _loc(admin, "z3-r0")["all"]] == ["gps", "declared"]


def test_network_estimate_needs_a_database_and_a_public_ip(mesh, admin, app, monkeypatch, ip):
    calls = []
    monkeypatch.setattr(location, "lookup_city", lambda path, addr: calls.append(addr) or {
        "label": "Example City, Example Country", "lat": 1.0, "lon": 2.0, "accuracy_km": 20})
    z3 = mesh.member("z3-r0")
    z3.login()  # seen from a private address

    assert _loc(admin, "z3-r0")["all"] == []  # no database configured
    app.state.config.geoip_db_path = "/nonexistent/city.mmdb"
    assert _loc(admin, "z3-r0")["all"] == []  # private address: never looked up
    assert calls == []

    ip["ip"] = "8.8.8.8"
    z3.append("event", summary="from outside")
    z3.flush()
    (net,) = _loc(admin, "z3-r0")["all"]
    assert (net["source"], net["label"], net["accuracy_km"]) == ("network", "Example City, Example Country", 20)
    assert calls == ["8.8.8.8"]


def test_lookup_without_the_package_or_file_is_none():
    assert location.lookup_city("/nonexistent/city.mmdb", "8.8.8.8") is None


def test_validation_audit_and_admin_only(mesh, admin, http, db_exec):
    mesh.member("z3-r0")
    for body in ({"label": "x", "lat": 91, "lon": 0}, {"label": "x", "lat": 0, "lon": 181}, {"label": ""},
                 {"label": "x" * 81}, {"label": "x", "lat": 1}, {"label": "x", "room": "r" * 41}):
        r = admin.put("/api/admin/locations/member/z3-r0", json=body)
        assert r.status_code == 422 and r.json()["error"]["code"] == "bad_location", body
    assert admin.put("/api/admin/locations/member/nobody", json={"label": "x"}).status_code == 404
    assert admin.put("/api/admin/locations/planet/z3-r0", json={"label": "x"}).status_code == 404

    admin.put("/api/admin/locations/member/z3-r0", json={"label": "Lab"})
    admin.put("/api/admin/locations/member/z3-r0", json={"label": "Lab"})  # unchanged: no audit row
    admin.delete("/api/admin/locations/member/z3-r0")

    async def audits(session):
        return [r.detail for r in await session.scalars(
            select(AuditLog).where(AuditLog.action == "location_change").order_by(AuditLog.id))]
    rows = db_exec(audits)
    assert [(r["old"] or {}).get("label") for r in rows] == [None, "Lab"]
    assert rows[1]["new"] is None

    assert http.get("/api/admin/locations", headers={"x-ltms-admin": "1"}).status_code == 401
    assert http.put("/api/admin/locations/member/z3-r0", json={"label": "x"},
                    headers={"x-ltms-admin": "1"}).status_code == 401


def test_the_server_location_needs_no_member_row(mesh, admin):
    assert admin.get("/api/admin/locations").json()["server"] is None
    r = admin.put("/api/admin/locations/member/ltms-server", json={"label": "Rack room", "lat": 1, "lon": 2})
    assert r.status_code == 200, r.text
    server = admin.get("/api/admin/locations").json()["server"]
    assert (server["source"], server["label"], server["lat"]) == ("declared", "Rack room", 1.0)


def _city(monkeypatch, app):
    calls = []
    monkeypatch.setattr(location, "lookup_city", lambda path, addr: calls.append(addr) or {
        "label": "Example City, Example Country", "lat": 1.0, "lon": 2.0, "accuracy_km": 20})
    app.state.config.geoip_db_path = "/nonexistent/city.mmdb"
    return calls


def test_relayed_member_takes_the_hubs_estimate_and_swarm_mates_inherit(mesh, admin, app, db_exec, monkeypatch, ip):
    """#346: a member on the LAN behind a hub is placed by the hub's public address."""
    calls = _city(monkeypatch, app)
    home = Home(mesh, db_exec)
    z3, vector = mesh.member("z3-r0"), mesh.member("vector")
    z3.login()
    vector.login()            # both only ever seen from the LAN
    home.add(z3)
    home.add(vector)
    ip["ip"] = "8.8.8.8"
    hub = mesh.member("jetson-hub", kind="relay")
    home.appoint(hub)
    hub.relay(sign(z3, 1))   # the hub reaches LTMS from a public address

    z = _loc(admin, "z3-r0")
    assert z["best"]["source"] == "network" and z["best"]["via_hub"] == "jetson-hub" and "unplaced" not in z
    assert calls and set(calls) == {"8.8.8.8"}
    # vector never had a record relayed, but its swarm has an estimate now.
    v = _loc(admin, "vector")
    assert v["best"]["lat"] == 1.0 and v["best"]["inherited_from"] == "swarm echo-base (network estimate from jetson-hub)"
    # A declared swarm home replaces the estimate for members with nothing better.
    admin.put("/api/admin/locations/swarm/echo-base", json={"label": "Home", "lat": 5.0, "lon": 6.0})
    assert _loc(admin, "vector")["best"]["source"] == "declared"


def test_unplaced_reasons_and_geo_status(mesh, admin, app, ip):
    ip["ip"] = "10.0.0.50"
    hub = mesh.member("jetson-hub", kind="relay")
    hub.login()
    r = admin.get("/api/admin/locations").json()
    assert r["geo"]["enabled"] is False and "LTMS_GEOIP_DB" in r["geo"]["reason"]
    (m,) = [x for x in r["members"] if x["name"] == "jetson-hub"]
    assert "10.0.0.50" in m["unplaced"] and "can't be located" in m["unplaced"]

    app.state.config.geoip_db_path = "/nonexistent/city.mmdb"
    geo = admin.get("/api/admin/locations").json()["geo"]
    assert geo["enabled"] is False and geo["reason"]  # extra missing or file missing: either way, said plainly
