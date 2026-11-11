"""Profile summary for a member or a swarm (issue #186)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from server.models import SwarmMember
from server.tests.test_swarms import Home, sign


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _summary(admin, **params):
    r = admin.get("/api/admin/profile/summary", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _seed(mesh):
    z3 = mesh.member("z3-r0")
    vec = mesh.member("vector", permissions=["contribute", "recall", "policy"])
    z3.append("perception", summary="tof", tags=["telemetry"], place="kitchen", payload={"kind": "range"})
    z3.append("perception", summary="wall ahead", place="kitchen")
    z3.append("thought", summary="go around")
    z3.append("decision", summary="left", refs={"because": "z3-r0:2"})
    z3.append("action", summary="drove", outcome="success", place="kitchen")
    z3.append("action", summary="bumped", outcome="stuck", place="hall")
    z3.append("action", summary="no outcome")
    z3.append("communication", summary="told vector", peers=["vector"])
    z3.append("lesson", summary="go left at the wall", refs={"derived_from": "vector:1"})
    z3.flush()
    vec.append("communication", summary="hi z3", peers=["z3-r0"])
    vec.flush()
    return z3, vec


def test_categories_flow_and_outcomes(mesh, admin):
    _seed(mesh)
    s = _summary(admin, member="z3-r0")
    assert s["subject"] == {"type": "member", "name": "z3-r0"} and s["hours"] == 168
    assert s["categories"] == {"sense": 2, "act": 5, "comm": 1, "learn": 1}
    assert s["flow"] == {"sensed": 1, "perceived": 1, "reasoned": 1, "decided": 1, "performed": 3, "learned": 1}
    assert s["outcomes"]["success"] == 1 and s["outcomes"]["stuck"] == 1 and s["outcomes"]["failed"] == 0
    assert s["action_success"] == 0.5  # actions without an outcome don't count
    assert [p["place"] for p in s["places"]] == ["kitchen", "hall"]
    kitchen = s["places"][0]
    assert kitchen["total"] == 3 and kitchen["sense"] == 2 and kitchen["act"] == 1 and kitchen["action_success"] == 1.0
    assert s["lessons"][0]["summary"] == "go left at the wall"
    # vector: z3's message to it, its message to z3, and z3's lesson derived from vector's record.
    assert s["peers"] == [{"name": "vector", "sense": 0, "act": 0, "comm": 2, "learn": 1}]


def test_daily_is_zero_filled_and_all_time_works(mesh, admin):
    _seed(mesh)
    s = _summary(admin, member="z3-r0", hours=72)
    assert len(s["daily"]) in (3, 4)  # three days back, plus today
    assert sum(d["act"] for d in s["daily"]) == 5
    assert sum(1 for d in s["daily"] if sum(v for k, v in d.items() if k != "day") == 0) >= 2
    today = datetime.now(timezone.utc).date().isoformat()
    assert s["daily"][-1]["day"] == today

    old_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    m = mesh.member("old-timer")
    m.append("event", summary="long ago", ts=old_ts)
    m.append("event", summary="today")
    m.flush()
    all_time = _summary(admin, member="old-timer", hours=0)
    assert all_time["hours"] == 0
    # Over 90 days: weekly buckets, keyed by each week's Monday.
    assert all(datetime.fromisoformat(d["day"]).weekday() == 0 for d in all_time["daily"])
    assert sum(d["act"] for d in all_time["daily"]) == 2
    week = _summary(admin, member="old-timer")
    assert sum(d["act"] for d in week["daily"]) == 1


def test_swarm_subject_includes_relayed_and_direct_records(mesh, admin, db_exec, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    hub.relay(sign(z3, 2))
    # z3 leaves; its relayed records still belong to the swarm by provenance.
    db_exec(update(SwarmMember).where(SwarmMember.member_id == uuid.UUID(z3.member_id)).values(left_at=datetime.now(timezone.utc)))
    dx = mesh.member("dx-1")
    home.add(dx)
    dx.append("decision", summary="direct")
    dx.flush()
    outsider = mesh.member("outsider")
    outsider.append("event", summary="not in the swarm")
    outsider.flush()
    s = _summary(admin, swarm="echo-base")
    assert s["subject"] == {"type": "swarm", "name": "echo-base"}
    assert s["categories"]["act"] == 3
    assert s["peers"] == []  # member subjects only
    assert s["members"] == [{"name": "z3-r0", "sense": 0, "act": 2, "comm": 0, "learn": 0},
                            {"name": "dx-1", "sense": 0, "act": 1, "comm": 0, "learn": 0}]


def test_action_success_is_null_without_outcomes(mesh, admin):
    m = mesh.member("z3-r0")
    m.append("action", summary="drove")
    m.flush()
    assert _summary(admin, member="z3-r0")["action_success"] is None


def test_subject_validation(mesh, admin, http, home):
    mesh.member("z3-r0")
    for params, status, code in (
        ({}, 422, "bad_subject"),
        ({"member": "z3-r0", "swarm": "echo-base"}, 422, "bad_subject"),
        ({"member": "nobody"}, 404, "not_found"),
        ({"swarm": "nowhere"}, 404, "not_found"),
        ({"member": "z3-r0", "hours": 9000}, 422, "bad_hours"),
        ({"member": "z3-r0", "hours": -1}, 422, "bad_hours"),
    ):
        r = admin.get("/api/admin/profile/summary", params=params)
        assert r.status_code == status, params
        assert r.json()["error"]["code"] == code
    r = http.get("/api/admin/profile/summary", params={"member": "z3-r0"}, headers={"x-ltms-admin": "1"})
    assert r.status_code == 401
