"""Renaming a swarm (issue #231)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from server.models import AuditLog, Swarm
from server.tests.test_recall_swarm import _setup
from server.tests.test_swarms import Home


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _rename(admin, old, new):
    return admin.patch(f"/api/admin/swarms/{old}", json={"name": new})


def test_rename_keeps_id_members_records_and_scopes(mesh, admin, db_exec, home):
    _hub, z3, _free, watcher = _setup(mesh, db_exec, home)
    admin.put("/api/admin/locations/swarm/echo-base", json={"label": "Site A"})
    before = [s for s in admin.get("/api/admin/swarms").json() if s["name"] == "echo-base"][0]

    r = _rename(admin, "echo-base", "hoth-base")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == home.id and r.json()["name"] == "hoth-base"

    after = [s for s in admin.get("/api/admin/swarms").json() if s["name"] == "hoth-base"][0]
    assert after["id"] == before["id"] and after["members"] == before["members"]
    # The scoped watcher listed "echo-base": its scope follows, so it still sees the swarm's records.
    assert len(watcher.records()) == 2
    assert admin.get("/api/admin/locations").json()["swarms"] == {
        "hoth-base": {"source": "declared", "label": "Site A", "room": None, "lat": None, "lon": None,
                      "accuracy_km": None}}
    assert _rename(admin, "echo-base", "x").status_code == 404

    async def audit(session):
        return [r.detail for r in await session.scalars(select(AuditLog).where(AuditLog.action == "swarm_rename"))]
    assert db_exec(audit) == [{"swarm": home.id, "old": "echo-base", "new": "hoth-base"}]


def test_name_rules(mesh, admin, db_exec, home):
    Home(mesh, db_exec, name="other")
    assert _rename(admin, "echo-base", "other").json()["error"]["code"] == "name_taken"
    assert _rename(admin, "echo-base", "Bad Name!").json()["error"]["code"] == "bad_name"
    assert _rename(admin, "echo-base", "echo-base").status_code == 200  # no-op

    async def names(session):
        return sorted(s.name for s in await session.scalars(select(Swarm)))
    assert db_exec(names) == ["echo-base", "other"]


def test_admin_only(mesh, http, home):
    r = http.patch("/api/admin/swarms/echo-base", json={"name": "x"}, headers={"x-ltms-admin": "1"})
    assert r.status_code == 401
