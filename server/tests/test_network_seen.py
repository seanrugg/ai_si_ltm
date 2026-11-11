"""Addresses LTMS sees for each member (issue #189)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from server.models import AuditLog, MemberAddress
from server.services import network
from server.tests.test_swarms import Home, sign


@pytest.fixture
def ip(monkeypatch):
    """Set the client address every request appears to come from."""
    current = {"ip": "192.168.1.20"}
    for mod in ("server.api.public", "server.api.member"):
        monkeypatch.setattr(f"{mod}.client_ip", lambda request: current["ip"])
    return current


def _network(admin, **params):
    r = admin.get("/api/admin/network", params=params)
    assert r.status_code == 200, r.text
    return {m["name"]: m for m in r.json()["members"]}


def _rows(db_exec, name=None):
    async def go(session):
        return list(await session.scalars(select(MemberAddress).order_by(MemberAddress.id)))
    return db_exec(go)


def test_login_and_ingest_record_the_address(mesh, admin, db_exec, ip):
    z3 = mesh.member("z3-r0")
    z3.login()
    z3.append("event", summary="hello")
    z3.flush()
    (row,) = _rows(db_exec)
    assert (row.ip, row.source, row.count) == ("192.168.1.20", "seen", 2)  # login + one batch
    m = _network(admin)["z3-r0"]
    assert m["seen"]["ip"] == "192.168.1.20" and m["seen"]["private"] is True
    assert m["path"] == "direct" and m["role"] == "free" and m["kind"] == "robot" and m["reported"] == []

    ip["ip"] = "8.8.8.8"
    z3.append("event", summary="moved")
    z3.flush()
    m = _network(admin)["z3-r0"]
    assert m["seen"]["ip"] == "8.8.8.8" and m["seen"]["private"] is False
    assert [h["ip"] for h in m["history"]] == ["8.8.8.8", "192.168.1.20"]

    async def changes(session):
        return [r.detail for r in await session.scalars(
            select(AuditLog).where(AuditLog.action == "network_change"))]
    assert db_exec(changes) == [{"source": "seen", "old": "192.168.1.20", "new": "8.8.8.8"}]


def test_a_relayed_batch_records_the_hub_only(mesh, admin, db_exec, ip):
    home = Home(mesh, db_exec)
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    ip["ip"] = "10.0.0.2"
    hub.relay(sign(z3, 2))
    rows = _rows(db_exec)
    assert {r.member_id for r in rows} == {uuid.UUID(hub.member_id)}
    net = _network(admin)
    assert net["z3-r0"]["path"] == "via jetson-hub" and net["z3-r0"]["seen"] is None
    assert net["jetson-hub"]["seen"]["ip"] == "10.0.0.2" and net["jetson-hub"]["role"] == "hub"
    assert set(_network(admin, swarm="echo-base")) == {"z3-r0", "jetson-hub"}
    assert set(_network(admin, member="z3-r0")) == {"z3-r0"}


def test_pruning_removes_old_rows_only(mesh, db_exec, ip):
    z3 = mesh.member("z3-r0")
    z3.login()
    ip["ip"] = "10.0.0.9"
    z3.append("event", summary="x")
    z3.flush()
    old = datetime.now(timezone.utc) - timedelta(days=200)
    db_exec(update(MemberAddress).where(MemberAddress.ip == "192.168.1.20").values(last_seen=old))
    assert db_exec(lambda s: network.prune(s, 180)) == 1
    assert [r.ip for r in _rows(db_exec)] == ["10.0.0.9"]


def test_admin_only_and_unknown_filters(mesh, admin, http):
    r = http.get("/api/admin/network", headers={"x-ltms-admin": "1"})
    assert r.status_code == 401
    assert admin.get("/api/admin/network", params={"swarm": "nowhere"}).status_code == 404
    assert admin.get("/api/admin/network", params={"member": "nobody"}).status_code == 404
