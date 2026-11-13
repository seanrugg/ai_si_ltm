"""Leader directives (issue #310)."""
from __future__ import annotations

import json

import pytest

from cli_agent.directives import DirectiveHandler
from cli_agent.client import LTMSError
from ltms_contract import public_key_hex
from scripts.swarm_key import leader_appointment
from server.services import fleet_key
from server.tests.test_directive_acks import Link
from server.tests.test_datasets_read import _get
from server.tests.test_swarms import Home


@pytest.fixture(autouse=True)
def fleet_key_file(app, tmp_path):
    app.state.config.fleet_key_path = str(tmp_path / "fleet-key.json")
    yield
    fleet_key._cache.clear()


def _lead(admin, home, member):
    r = admin.post(f"/api/admin/swarms/{home.name}/leader",
                   json=leader_appointment(home.root, home.id, member.name, public_key_hex(member.key)))
    assert r.status_code == 200, r.text


def _post(member, path, body=None):
    member.login()
    return member.http.post(path, json=body, headers={"Authorization": f"Bearer {member.token}"})


def test_leader_issues_and_members_verify_with_its_key(mesh, admin, db_exec, tmp_path):
    home = Home(mesh, db_exec)
    z3, claude = mesh.member("z3-r0"), mesh.member("claude", kind="assistant", permissions=("contribute",))
    home.add(z3)
    home.add(claude)
    _lead(admin, home, claude)
    d = claude.issue_directive("Patrol the kitchen", expires_in=3600)
    assert d["signer"] == "leader" and d["issued_by"] == "claude" and d["scope"]["swarm_name"] == "echo-base"

    leader_pub = _get(z3, "/api/swarm").json()["swarm"]["leader"]["public_key"]
    h = DirectiveHandler(Link(z3), lambda: None, str(tmp_path / "z3.json"), leader_key=lambda: leader_pub)
    wire = json.dumps({"kind": "directive", "id": d["id"], "statement": d["statement"],
                       "signature": d["signature"], "signer": "leader"}).encode()
    assert h.on_message(wire) == "accepted"
    (row,) = admin.get("/api/admin/directives").json()["directives"]
    assert row["delivery"]["accepted"] == 1


def test_non_leaders_and_other_swarms_are_refused(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    z3 = mesh.member("z3-r0")
    home.add(z3)
    with pytest.raises(LTMSError) as e:
        z3.issue_directive("Obey me")
    assert e.value.status == 403
    claude = mesh.member("claude", kind="assistant", permissions=("contribute",))
    _lead(admin, home, claude)
    away = Home(mesh, db_exec, name="away-team")
    from ltms_contract import canonicalize
    stmt = {"v": 1, "id": "00000000-0000-4000-8000-000000000001", "scope": {"swarm": away.id}, "text": "x",
            "payload": None, "issued_by": "claude", "issued_at": "2026-01-01T00:00:00Z", "expires_at": "2026-01-02T00:00:00Z"}
    raw = canonicalize(stmt)
    sig = claude.key.sign(b"ltms-directive-v1\n" + raw).signature.hex()
    r = _post(claude, "/api/directives", {"statement": raw.decode(), "signature": sig})
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_your_swarm"


def test_leader_change_inherits_open_directives(mesh, admin, db_exec):
    home = Home(mesh, db_exec)
    a = mesh.member("alpha", kind="assistant", permissions=("contribute",))
    b = mesh.member("bravo", kind="assistant", permissions=("contribute",))
    _lead(admin, home, a)
    d1 = a.issue_directive("one")
    d2 = a.issue_directive("two")
    _lead(admin, home, b)
    b.login()
    inh = b.http.get("/api/directives", params={"inherited": 1}, headers={"Authorization": f"Bearer {b.token}"}).json()
    assert {x["id"] for x in inh["directives"]} == {d1["id"], d2["id"]}
    c1 = _post(b, f"/api/directives/{d1['id']}/confirm").json()
    assert c1["inherited"] is False and c1["confirmed_by"] == "bravo"
    c2 = _post(b, f"/api/directives/{d2['id']}/cancel").json()
    assert c2["status"] == "cancelled" and c2["cancel"]["signature"]
    assert _post(a, f"/api/directives/{d1['id']}/cancel").status_code == 403  # no longer the leader
    # The fleet outranks the leader: an admin can cancel a leader's directive.
    d3 = b.issue_directive("three")
    assert admin.post(f"/api/admin/directives/{d3['id']}/cancel").json()["status"] == "cancelled"
