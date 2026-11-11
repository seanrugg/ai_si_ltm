"""Listing one member's tombstones by seq range (issue #205)."""
from __future__ import annotations


def _member_with_deletions(mesh, admin, name, n=6, delete=(2, 4, 5)):
    m = mesh.member(name)
    for i in range(1, n + 1):
        m.append("event", summary=f"{name} {i}")
    m.flush()
    for seq in delete:
        r = admin.request("DELETE", f"/api/admin/records/{name}/{seq}", json={"reason": f"r{seq}"})
        assert r.status_code == 200, r.text
    return m


def _seqs(admin, **params):
    r = admin.get("/api/admin/tombstones", params=params)
    assert r.status_code == 200, r.text
    return [t["seq"] for t in r.json()["tombstones"]]


def test_lists_a_members_tombstones_newest_first(mesh, admin):
    _member_with_deletions(mesh, admin, "z3-r0")
    _member_with_deletions(mesh, admin, "dx-1", delete=(3,))
    r = admin.get("/api/admin/tombstones", params={"agent": "z3-r0"})
    body = r.json()["tombstones"]
    assert [t["seq"] for t in body] == [5, 4, 2]
    assert all(t["deleted"] is True and t["agent"] == "z3-r0" for t in body)
    assert body[0]["reason"] == "r5" and body[0]["deleted_by"].startswith("admin:")


def test_range_bounds_are_inclusive(mesh, admin):
    _member_with_deletions(mesh, admin, "z3-r0")
    assert _seqs(admin, agent="z3-r0", seq_from=2, seq_to=4) == [4, 2]
    assert _seqs(admin, agent="z3-r0", seq_from=4) == [5, 4]
    assert _seqs(admin, agent="z3-r0", seq_to=3) == [2]
    assert _seqs(admin, agent="z3-r0", seq_from=6, seq_to=9) == []


def test_other_agents_and_unknown_agents(mesh, admin):
    _member_with_deletions(mesh, admin, "z3-r0")
    _member_with_deletions(mesh, admin, "dx-1", delete=(3,))
    assert _seqs(admin, agent="dx-1") == [3]
    assert _seqs(admin, agent="nobody") == []


def test_agent_is_required(admin):
    r = admin.get("/api/admin/tombstones")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_agent"


def test_admin_only(http):
    r = http.get("/api/admin/tombstones", params={"agent": "z3-r0"})
    assert r.status_code in (401, 403)
