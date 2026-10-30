"""recall:scoped limited to swarms, by record provenance (issue #44)."""
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
    watcher = mesh.member("watcher", permissions=("recall:scoped",),
                          scope={"swarms": ["echo-base"]})
    return hub, z3, free, watcher


def test_scoped_recall_sees_only_its_swarms(mesh, db_exec, home):
    _hub, z3, _free, watcher = _setup(mesh, db_exec, home)
    seen = watcher.records()
    assert len(seen) == 2
    assert [r["agent"] for r in seen] == ["z3-r0", "z3-r0"]
    assert {r["summary"] for r in seen} == {"z3-r0 #0", "z3-r0 #1"}


def test_moving_a_member_keeps_old_provenance_visible(mesh, db_exec, home):
    _hub, z3, _free, watcher = _setup(mesh, db_exec, home)
    other = Home(mesh, db_exec, name="other")
    other.add(z3)  # move z3-r0 out of echo-base
    z3.append("event", summary="after the move")
    z3.flush()  # direct write, stamped with the new swarm
    seen = watcher.records()
    assert len(seen) == 2
    assert [r["agent"] for r in seen] == ["z3-r0", "z3-r0"]


def test_empty_swarm_list_sees_nothing(mesh, db_exec, home):
    _setup(mesh, db_exec, home)
    blind = mesh.member("blind-swarm", permissions=("recall:scoped",),
                        scope={"swarms": []})
    assert blind.records() == []


def test_swarms_must_be_a_list(mesh, admin, db_exec, home):
    _setup(mesh, db_exec, home)
    bad = mesh.member("bad-watcher", permissions=("recall:scoped",), approve=False)
    r = admin.post(f"/api/admin/members/{bad.member_id}/approve",
                   json={"scope": {"swarms": "echo-base"}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_scope"


def test_full_recall_still_sees_everything(mesh, db_exec, home):
    _setup(mesh, db_exec, home)
    full = mesh.member("full-view", permissions=["contribute", "recall"])
    seen = full.records(limit=200)
    assert len(seen) == 3
    assert {r["agent"] for r in seen} == {"z3-r0", "free-agent"}
