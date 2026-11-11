"""Interaction graph: nodes, per-category edges, hub relays (issue #187)."""
from __future__ import annotations

import pytest

from server.tests.test_swarms import Home, sign


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _graph(admin, **params):
    r = admin.get("/api/admin/profile/graph", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _edges(g):
    return {(e["a"], e["b"], e["category"]): e["count"] for e in g["edges"]}


def test_communication_edges_merge_both_directions(mesh, admin):
    z3, vec = mesh.member("z3-r0"), mesh.member("vector")
    z3.append("communication", summary="hi", peers=["vector"])
    z3.append("communication", summary="again", peers=["vector"])
    z3.flush()
    vec.append("communication", summary="hello", peers=["z3-r0"])
    vec.flush()
    g = _graph(admin, member="z3-r0")
    assert _edges(g) == {("vector", "z3-r0", "comm"): 3}
    nodes = {n["name"]: n for n in g["nodes"]}
    assert nodes["vector"]["role"] == "free" and nodes["vector"]["kind"] == "robot"
    assert nodes["z3-r0"]["cognition"] == {"value": None, "source": None, "offboard_share": None}


def test_a_lesson_referencing_anothers_action_is_a_learn_edge(mesh, admin):
    z3, vec = mesh.member("z3-r0"), mesh.member("vector")
    act = vec.append("action", summary="drove", outcome="stuck")
    vec.flush()
    z3.append("lesson", summary="avoid the rug", refs={"because": [f"vector:{act['seq']}"]})
    z3.append("decision", summary="own cause", refs={"because": "z3-r0:1"})  # same author: no edge
    z3.flush()
    assert _edges(_graph(admin, member="vector")) == {("vector", "z3-r0", "learn"): 1}


def test_relayed_records_are_relays_not_edges(mesh, admin, db_exec, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    hub.relay(sign(z3, 3))
    g = _graph(admin, member="z3-r0")
    assert g["edges"] == []
    assert g["relays"] == [{"hub": "jetson-hub", "member": "z3-r0", "count": 3}]
    nodes = {n["name"]: n for n in g["nodes"]}
    assert nodes["jetson-hub"]["is_hub"] is True and nodes["jetson-hub"]["swarm"] == "echo-base"
    assert _graph(admin, member="jetson-hub")["relays"] == g["relays"]


def test_a_swarm_includes_an_outside_member_it_talks_to(mesh, admin, home):
    z3, dx, outsider = mesh.member("z3-r0"), mesh.member("dx-1"), mesh.member("outsider")
    home.add(z3)
    home.add(dx)
    z3.append("communication", summary="hi", peers=["outsider", "dx-1"])
    z3.flush()
    loner = mesh.member("loner")
    loner.append("communication", summary="nobody here", peers=["outsider"])
    loner.flush()
    g = _graph(admin, swarm="echo-base")
    assert _edges(g) == {("outsider", "z3-r0", "comm"): 1, ("dx-1", "z3-r0", "comm"): 1}
    assert {n["name"] for n in g["nodes"]} == {"z3-r0", "dx-1", "outsider"}


def test_subject_validation(mesh, admin, http):
    mesh.member("z3-r0")
    for params, status, code in (
        ({}, 422, "bad_subject"),
        ({"member": "z3-r0", "swarm": "echo-base"}, 422, "bad_subject"),
        ({"member": "nobody"}, 404, "not_found"),
    ):
        r = admin.get("/api/admin/profile/graph", params=params)
        assert r.status_code == status and r.json()["error"]["code"] == code
    r = http.get("/api/admin/profile/graph", params={"member": "z3-r0"}, headers={"x-ltms-admin": "1"})
    assert r.status_code == 401
