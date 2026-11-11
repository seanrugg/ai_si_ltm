"""Profile "What it read": dataset reads and dream cycles (issue #243)."""
from __future__ import annotations

import pytest

from server.tests.test_datasets_read import _dataset, data_dir  # noqa: F401
from server.tests.test_swarms import Home


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _reading(admin, **params):
    r = admin.get("/api/admin/profile/reading", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _cycle(member, did, keys, lessons):
    with member.dream_cycle(jitter_s=0) as cycle:
        for k in keys:
            cycle.read(did, k)
        for summary in lessons:
            cycle.lesson(summary, sources=[{"dataset": did, "version": 1, "item": keys[0]}])
    return cycle


def test_reads_and_cycles_per_member_and_swarm(mesh, admin, home):
    did = _dataset(admin)
    z3, vector = mesh.member("z3-r0"), mesh.member("vector")
    for m in (z3, vector):
        m.login()
        home.add(m)
    first = _cycle(z3, did, ["drive.md", "notes.txt"], ["Slow down near the dock"])
    z3.read_item(did, "drive.md")  # outside a cycle: still a read
    z3.append("lesson", summary="unrelated lesson")
    z3.flush()
    second = _cycle(z3, did, ["notes.txt"], ["Check the wheel", "Log the stall"])
    _cycle(vector, did, ["drive.md"], [])

    mine = _reading(admin, member="z3-r0")
    assert mine["datasets"] == [{"dataset": did, "name": "Manuals", "reads": 4, "items": 2,
                                 "last_read": mine["datasets"][0]["last_read"]}]
    assert [c["cycle"] for c in mine["cycles"]] == [second.id, first.id]  # newest first
    newest, oldest = mine["cycles"]
    assert [l["summary"] for l in newest["lessons"]] == ["Check the wheel", "Log the stall"]
    assert [l["summary"] for l in oldest["lessons"]] == ["Slow down near the dock"]
    assert oldest["items_read"] == 2 and newest["started"] and newest["ended"]
    assert all(l["agent"] == "z3-r0" and l["seq"] for l in newest["lessons"])

    swarm = _reading(admin, swarm="echo-base")
    assert swarm["datasets"][0]["reads"] == 5
    assert len(swarm["cycles"]) == 3 and {c["agent"] for c in swarm["cycles"]} == {"z3-r0", "vector"}


def test_nothing_read(mesh, admin):
    mesh.member("z3-r0")
    out = _reading(admin, member="z3-r0", hours=24)
    assert out["datasets"] == [] and out["cycles"] == [] and out["hours"] == 24


def test_subject_validation(mesh, admin):
    assert admin.get("/api/admin/profile/reading").status_code == 422
    assert admin.get("/api/admin/profile/reading", params={"member": "z3-r0", "hours": -1}).status_code == 422
