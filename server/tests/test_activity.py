"""Activity aggregate endpoint (issue #73)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def _activity(mesh, admin, **params):
    r = admin.get("/api/admin/activity", params=params)
    assert r.status_code == 200, r.text
    return {g["key"]: g for g in r.json()["groups"]}


def test_group_by_swarm(mesh, admin, db_exec, home):
    _setup(mesh, db_exec, home)
    groups = _activity(mesh, admin, group="swarm")
    assert set(groups) == {"echo-base", None}
    echo = groups["echo-base"]
    assert echo["total"] == 2
    assert echo["by_type"] == {"event": 2}
    assert sum(echo["series"]) == 2
    assert len(echo["series"]) == 24
    free = groups[None]
    assert free["total"] == 1
    assert free["by_type"] == {"event": 1}
    assert sum(free["series"]) == 1
    assert len(free["series"]) == 24


def test_telemetry_counted(mesh, admin, db_exec, home):
    _hub, z3, _free = _setup(mesh, db_exec, home)
    z3.append("event", summary="telemetry", tags=["telemetry"])
    z3.flush()
    groups = _activity(mesh, admin, group="swarm")
    echo = groups["echo-base"]
    assert echo["total"] == 3
    assert echo["telemetry"] == 1
    assert echo["by_type"] == {"event": 3}
    assert len(echo["telemetry_series"]) == 24
    assert sum(echo["telemetry_series"]) == echo["telemetry"]
    for i in range(24):
        assert echo["telemetry_series"][i] <= echo["series"][i]


def test_group_by_member_swarm_filter(mesh, admin, db_exec, home):
    _setup(mesh, db_exec, home)
    groups = _activity(mesh, admin, group="member", swarm="echo-base")
    assert set(groups) == {"z3-r0"}
    assert groups["z3-r0"]["total"] == 2

    groups = _activity(mesh, admin, group="member", swarm="none")
    assert set(groups) == {"free-agent"}
    assert groups["free-agent"]["total"] == 1


def test_previous_total(mesh, admin, db_exec, home):
    _hub, z3, _free = _setup(mesh, db_exec, home)
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    z3.append("event", summary="old", ts=old.isoformat())
    z3.flush()
    r = admin.get("/api/admin/activity", params={"group": "swarm", "hours": 24})
    assert r.status_code == 200
    groups = {g["key"]: g for g in r.json()["groups"]}
    echo = groups["echo-base"]
    assert echo["total"] == 2
    assert echo["previous_total"] == 1


def test_validation(mesh, admin, db_exec, home):
    _setup(mesh, db_exec, home)
    assert admin.get("/api/admin/activity", params={"hours": 0}).status_code == 422
    assert admin.get("/api/admin/activity", params={"hours": 721}).status_code == 422
    assert admin.get("/api/admin/activity", params={"group": "foo"}).status_code == 422
