"""Free agents: the list endpoint and scoped recall including them (issue #66)."""
from __future__ import annotations

import pytest

from server.tests.test_swarms import Home, sign


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _setup(mesh, db_exec, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    hub.relay(sign(z3, 2))
    free = mesh.member("free-agent")
    free.append("event", summary="free")
    free.flush()
    return hub, z3, free


def test_list_free_agents(mesh, admin, db_exec, home):
    _hub, _z3, _free = _setup(mesh, db_exec, home)
    r = admin.get("/api/admin/members?free_agent=true")
    assert [m["name"] for m in r.json()["members"]] == ["free-agent"]
    r = admin.get("/api/admin/members?free_agent=true&status=active")
    assert [m["name"] for m in r.json()["members"]] == ["free-agent"]


def test_removed_member_becomes_free_agent(mesh, admin, db_exec, home):
    _hub, z3, _free = _setup(mesh, db_exec, home)
    admin.request("DELETE", f"/api/admin/swarms/members/{z3.name}")
    r = admin.get("/api/admin/members?free_agent=true")
    names = {m["name"] for m in r.json()["members"]}
    assert names == {"z3-r0", "free-agent"}


def test_scoped_recall_free_agents_only(mesh, db_exec, home):
    _hub, _z3, free = _setup(mesh, db_exec, home)
    watcher = mesh.member("watcher", permissions=("recall:scoped",),
                          scope={"types": ["event"], "swarms": [None]})
    seen = watcher.records()
    assert len(seen) == 1
    assert seen[0]["agent"] == "free-agent"
    assert seen[0]["summary"] == "free"


def test_scoped_recall_swarms_and_free_agents(mesh, db_exec, home):
    _hub, z3, free = _setup(mesh, db_exec, home)
    watcher = mesh.member("watcher", permissions=("recall:scoped",),
                          scope={"types": ["event"], "swarms": ["echo-base", None]})
    seen = watcher.records()
    assert len(seen) == 3
    assert {r["agent"] for r in seen} == {"z3-r0", "free-agent"}


def test_scoped_recall_empty_swarm_list_sees_nothing(mesh, db_exec, home):
    _setup(mesh, db_exec, home)
    blind = mesh.member("blind", permissions=("recall:scoped",),
                        scope={"types": ["event"], "swarms": []})
    assert blind.records() == []


def test_null_only_allowed_in_swarms(mesh, admin, db_exec, home):
    _setup(mesh, db_exec, home)
    bad = mesh.member("bad-watcher", permissions=("recall:scoped",), approve=False)
    r = admin.post(f"/api/admin/members/{bad.member_id}/approve",
                   json={"scope": {"types": [None]}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_scope"
