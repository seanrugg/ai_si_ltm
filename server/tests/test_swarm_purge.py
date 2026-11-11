"""Purging the records that came through a swarm (issue #233)."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from server.models import AuditLog, Record, RecordTombstone
from server.services import records as records_service
from server.tests.test_swarms import Home, sign


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _count(db_exec, model, **where):
    async def go(session):
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return await session.scalar(stmt)
    return db_exec(go)


def _setup(mesh, home, n=3):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    dx = mesh.member("dx-1")
    dx.append("event", summary="direct, never through the swarm")
    dx.flush()
    home.add(z3)
    home.appoint(hub)
    hub.relay(sign(z3, n))
    return hub, z3, dx


def test_preview_then_purge_only_the_swarms_records(mesh, admin, db_exec, home):
    _setup(mesh, home)
    assert admin.get("/api/admin/swarms/echo-base/remove-preview").json() == {"members": 2, "records": 3}
    r = admin.post("/api/admin/swarms/echo-base/remove",
                   json={"confirm": "echo-base", "members": "free", "records": "purge"})
    assert r.status_code == 200, r.text
    assert r.json()["purged"] == 3
    assert _count(db_exec, Record, agent="z3-r0") == 0
    assert _count(db_exec, RecordTombstone, agent="z3-r0") == 3
    assert _count(db_exec, Record, agent="dx-1") == 1  # direct record kept

    async def audit(session):
        return [(r.actor, r.detail) for r in await session.scalars(
            select(AuditLog).where(AuditLog.action == "records_purge"))]
    rows = db_exec(audit)
    assert len(rows) == 1 and rows[0][1]["count"] == 3 and rows[0][0].startswith("admin:")
    # The chain shows the gap as deleted, as with single deletes (#173).
    tl = admin.get("/api/admin/tombstones", params={"agent": "z3-r0"}).json()["tombstones"]
    assert [t["seq"] for t in tl] == [3, 2, 1]


def test_later_direct_records_of_the_same_agent_are_kept(mesh, admin, db_exec, home):
    _hub, z3, _dx = _setup(mesh, home, n=2)
    home_other = Home(mesh, db_exec, name="other")
    home_other.add(z3)  # moved out; later records are stamped with the new swarm
    z3.append("event", summary="after the move")
    z3.flush()
    admin.post("/api/admin/swarms/echo-base/remove", json={"confirm": "echo-base", "members": "free", "records": "purge"})
    async def left(session):
        return [r.summary for r in await session.scalars(select(Record).where(Record.agent == "z3-r0"))]
    assert db_exec(left) == ["after the move"]


def test_batches_and_the_limit(mesh, admin, db_exec, home, monkeypatch):
    _setup(mesh, home, n=7)
    calls = []
    real = records_service.delete_records_bulk

    async def spy(session, **kw):
        kw["batch"] = 3
        calls.append(kw)
        return await real(session, **kw)
    monkeypatch.setattr(records_service, "delete_records_bulk", spy)
    monkeypatch.setattr(records_service, "PURGE_MAX", 6)
    r = admin.post("/api/admin/swarms/echo-base/remove", json={"confirm": "echo-base", "members": "free", "records": "purge"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many"
    assert _count(db_exec, Record, agent="z3-r0") == 7  # nothing changed
    assert any(s["name"] == "echo-base" for s in admin.get("/api/admin/swarms").json())

    monkeypatch.setattr(records_service, "PURGE_MAX", 100)
    r = admin.post("/api/admin/swarms/echo-base/remove", json={"confirm": "echo-base", "members": "free", "records": "purge"})
    assert r.json()["purged"] == 7 and _count(db_exec, RecordTombstone, agent="z3-r0") == 7
