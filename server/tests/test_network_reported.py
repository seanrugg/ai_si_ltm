"""LAN addresses reported by members and their hubs (issue #190). Unverified."""
from __future__ import annotations

from sqlalchemy import select

from server.models import AuditLog
from server.tests.test_swarms import Home


def _put(client, path, addresses):
    client.login()
    return client.http.put(path, json={"addresses": addresses}, headers={"Authorization": f"Bearer {client.token}"})


def _reported(admin, name):
    r = admin.get("/api/admin/network", params={"member": name})
    assert r.status_code == 200, r.text
    return r.json()["members"][0]["reported"]


def _changes(db_exec):
    async def go(session):
        return [r.detail for r in await session.scalars(
            select(AuditLog).where(AuditLog.action == "network_change").order_by(AuditLog.id))]
    return db_exec(go)


def test_a_member_reports_two_addresses(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    stored = z3.report_network([{"ip": "192.168.1.41", "interface": "wlan0"}, {"ip": "fd00::41", "interface": "wlan0"}])
    assert [a["ip"] for a in stored] == ["192.168.1.41", "fd00::41"]
    rep = _reported(admin, "z3-r0")
    assert {(a["ip"], a["interface"], a["source"]) for a in rep} == {
        ("192.168.1.41", "wlan0", "reported"), ("fd00::41", "wlan0", "reported")}
    assert all(a["last_seen"] for a in rep)

    # The same set again: no change. A different set replaces it and is audited.
    z3.report_network([{"ip": "fd00::41", "interface": "wlan0"}, {"ip": "192.168.1.41", "interface": "wlan0"}])
    z3.report_network([{"ip": "192.168.1.77", "interface": "eth0"}])
    assert [a["ip"] for a in _reported(admin, "z3-r0")] == ["192.168.1.77"]
    assert _changes(db_exec) == [
        {"source": "reported", "old": [], "new": ["192.168.1.41", "fd00::41"]},
        {"source": "reported", "old": ["192.168.1.41", "fd00::41"], "new": ["192.168.1.77"]},
    ]


def test_a_hub_reports_for_its_member_only(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    r = _put(hub, "/api/relay/network/z3-r0", [{"ip": "192.168.4.12", "interface": None}])
    assert r.status_code == 200, r.text
    assert [(a["ip"], a["source"]) for a in _reported(admin, "z3-r0")] == [("192.168.4.12", "hub")]

    other = Home(mesh, db_exec, name="rogue-one")
    hub2, dx = mesh.member("rogue-hub", kind="relay"), mesh.member("dx-1")
    other.add(dx)
    other.appoint(hub2)
    r = _put(hub2, "/api/relay/network/z3-r0", [{"ip": "192.168.4.13"}])
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_in_swarm"
    assert _put(z3, "/api/relay/network/dx-1", [{"ip": "192.168.4.13"}]).status_code == 403  # not a hub


def test_validation(mesh):
    z3 = mesh.member("z3-r0")
    for addresses in ([{"ip": "not-an-ip"}], [{"ip": f"10.0.0.{i}"} for i in range(9)],
                      [{"ip": "10.0.0.1", "interface": "x" * 33}], "nope"):
        r = _put(z3, "/api/members/me/network", addresses)
        assert r.status_code == 422 and r.json()["error"]["code"] == "bad_network", addresses
    assert z3.report_network([{"ip": "nope"}]) is None  # logs, never raises
