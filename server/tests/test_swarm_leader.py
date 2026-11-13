"""Swarm leader (issue #308): appointed with the root key; gets scoped recall of the swarm."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ltms_contract import generate_key, public_key_hex
from scripts.swarm_key import leader_appointment
from server.tests.test_datasets_read import _get
from server.tests.test_swarms import Home


def _appoint(admin, home, member, *, root=None, swarm=None, ts=None, key=None):
    appt = leader_appointment(root or home.root, swarm or home.id, member.name, key or public_key_hex(member.key), ts=ts)
    return admin.post(f"/api/admin/swarms/{home.name}/leader", json=appt)


def _member_row(admin, name):
    return next(m for m in admin.get("/api/admin/members").json()["members"] if m["name"] == name)


def test_appoint_grants_scoped_recall_and_shows_in_swarm(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    z3 = mesh.member("z3-r0")
    home.add(z3)
    claude = mesh.member("claude", kind="assistant", permissions=("contribute",))  # a cloud agent may lead
    r = _appoint(admin, home, claude)
    assert r.status_code == 200, r.text
    assert r.json()["leader"]["name"] == "claude"
    row = _member_row(admin, "claude")
    assert "recall:scoped" in row["permissions"] and row["scope"]["swarms"] == ["echo-base"]
    assert "system" not in row["scope"]["types"] and "communication" in row["scope"]["types"]
    assert _get(z3, "/api/swarm").json()["swarm"]["leader"]["name"] == "claude"
    hist = admin.get("/api/admin/profile/history", params={"member": "claude"}).json()["events"]
    assert any(e["title"].startswith("Appointed leader of echo-base") for e in hist)


def test_refusals(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.appoint(hub)
    assert _appoint(admin, home, z3, root=generate_key()).json()["error"]["code"] == "bad_appointment"
    assert _appoint(admin, home, z3, swarm="f" * 64).json()["error"]["code"] == "bad_appointment"
    old = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert _appoint(admin, home, z3, ts=old).json()["error"]["code"] == "stale_appointment"
    assert _appoint(admin, home, z3, key="0" * 64).json()["error"]["code"] == "bad_appointment"
    assert _appoint(admin, home, hub).json()["error"]["code"] == "is_hub"


def test_replace_and_remove_restore_what_was_added(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    a = mesh.member("alpha", kind="assistant", permissions=("contribute",))
    b = mesh.member("bravo", kind="agent", permissions=("contribute", "recall"))  # already full recall
    before_a = (_member_row(admin, "alpha")["permissions"], _member_row(admin, "alpha")["scope"])
    _appoint(admin, home, a)
    _appoint(admin, home, b)  # replaces alpha
    assert (_member_row(admin, "alpha")["permissions"], _member_row(admin, "alpha")["scope"]) == before_a
    assert _member_row(admin, "bravo")["permissions"] == ["contribute", "recall"]
    r = admin.delete("/api/admin/swarms/echo-base/leader")
    assert r.json()["removed"] is True and _member_row(admin, "bravo")["permissions"] == ["contribute", "recall"]
    assert admin.delete("/api/admin/swarms/echo-base/leader").json()["removed"] is False
