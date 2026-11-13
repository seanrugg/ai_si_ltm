"""Members verify and acknowledge directives (issue #306)."""
from __future__ import annotations

import json

import pytest

from cli_agent.directives import Decision, DirectiveHandler
from server.services import fleet_key
from server.tests.test_swarms import Home


@pytest.fixture(autouse=True)
def fleet_key_file(app, tmp_path):
    app.state.config.fleet_key_path = str(tmp_path / "fleet-key.json")
    yield
    fleet_key._cache.clear()


class Link:
    """Signs through a test member, flushing each record."""

    def __init__(self, member):
        self.m = member

    def append(self, type, **kw):
        rec = self.m.append(type, **kw)
        self.m.flush()
        return rec


def _wire(d):
    return json.dumps({"kind": "directive", "id": d["id"], "statement": d["statement"],
                       "signature": d["signature"], "signer": "fleet"}).encode()


def test_received_then_accepted_and_counted(mesh, admin, http, db_exec, tmp_path):
    home = Home(mesh, db_exec)
    z3, vec = mesh.member("z3-r0"), mesh.member("vector")
    home.add(z3)
    home.add(vec)
    pub = http.get("/api/fleet-key").json()["public_key"]
    d = admin.post("/api/admin/directives", json={"scope": {"swarm": "echo-base"}, "text": "Dock by 22:00"}).json()

    h = DirectiveHandler(Link(z3), lambda: pub, str(tmp_path / "z3.json"))
    assert h.on_message(_wire(d)) == "accepted"
    assert h.on_message(_wire(d)) == "already"  # a retained re-delivery
    no = DirectiveHandler(Link(vec), lambda: pub, str(tmp_path / "v.json"),
                          decide=lambda stmt: Decision.decline("outside my safety envelope"))
    assert no.on_message(_wire(d)) == "declined"

    (row,) = admin.get("/api/admin/directives").json()["directives"]
    assert row["delivery"] == {"meant_for": 2, "received": 2, "accepted": 1, "declined": 1,
                               "declines": [{"member": "vector", "reason": "outside my safety envelope"}]}


def test_tampered_directive_is_ignored_and_logged(mesh, admin, http, db_exec, tmp_path):
    z3 = mesh.member("z3-r0")
    pub = http.get("/api/fleet-key").json()["public_key"]
    d = admin.post("/api/admin/directives", json={"scope": {"all": True}, "text": "Hold"}).json()
    bad = {**d, "statement": d["statement"].replace("Hold", "Drive off the table")}
    h = DirectiveHandler(Link(z3), lambda: pub, str(tmp_path / "z3.json"))
    assert h.on_message(_wire(bad)) == "invalid"
    recs = admin.get("/api/admin/timeline", params={"agent": "z3-r0"}).json()["records"]
    assert [r["tags"] for r in recs] == [["directive-invalid"]]
    assert h.on_message(b"") == "cleared"


def test_a_decline_needs_a_reason():
    with pytest.raises(ValueError):
        Decision.decline("")
    with pytest.raises(ValueError):
        Decision(False)
    assert Decision.decline("charter section 2").reason == "charter section 2"
