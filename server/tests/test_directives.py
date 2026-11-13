"""Directives (issue #303): signed by the fleet key, to one swarm or all."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from server.services import directives, fleet_key
from server.tests.test_swarms import Home
import pytest


@pytest.fixture(autouse=True)
def fleet_key_file(app, tmp_path):
    app.state.config.fleet_key_path = str(tmp_path / "fleet-key.json")
    yield
    fleet_key._cache.clear()


def test_issue_to_a_swarm_and_to_all_and_verify(mesh, admin, http, db_exec):
    Home(mesh, db_exec)
    pub = http.get("/api/fleet-key").json()["public_key"]
    one = admin.post("/api/admin/directives", json={"scope": {"swarm": "echo-base"}, "text": "Dock by 22:00"})
    assert one.status_code == 201, one.text
    d = one.json()
    assert d["status"] == "open" and d["scope"]["swarm_name"] == "echo-base" and d["issued_by"].startswith("admin:")
    assert fleet_key.verify(pub, directives.DOMAIN, d["statement"].encode(), d["signature"])
    assert not fleet_key.verify(pub, directives.DOMAIN, d["statement"].replace("22:00", "23:00").encode(), d["signature"])
    assert '"v":1' in d["statement"] and f'"id":"{d["id"]}"' in d["statement"]
    every = admin.post("/api/admin/directives", json={"scope": {"all": True}, "text": "Report health", "expires_in_days": 1}).json()
    assert every["scope"] == {"all": True}
    assert {x["id"] for x in admin.get("/api/admin/directives").json()["directives"]} == {every["id"], d["id"]}
    for bad in ({"scope": {"swarm": "nope"}, "text": "x"}, {"scope": {}, "text": "x"}, {"scope": {"all": True}, "text": ""},
                {"scope": {"all": True}, "text": "x" * 2001}, {"scope": {"all": True}, "text": "x", "expires_in_days": 0}):
        assert admin.post("/api/admin/directives", json=bad).status_code in (404, 422), bad


def test_cancel_is_a_signed_statement_and_expiry_is_reported(mesh, admin, http):
    pub = http.get("/api/fleet-key").json()["public_key"]
    d = admin.post("/api/admin/directives", json={"scope": {"all": True}, "text": "Hold"}).json()
    c = admin.post(f"/api/admin/directives/{d['id']}/cancel").json()
    assert c["status"] == "cancelled" and c["statement"] == d["statement"]
    assert fleet_key.verify(pub, directives.CANCEL_DOMAIN, c["cancel"]["statement"].encode(), c["cancel"]["signature"])
    assert admin.post(f"/api/admin/directives/{d['id']}/cancel").status_code == 409
    old = type("D", (), {"status": "open", "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})()
    assert directives.status_of(old) == "expired"


def test_fleet_key_is_created_once_with_mode_0600(tmp_path):
    cfg = type("C", (), {"fleet_key_path": str(tmp_path / "fk.json"), "data_dir": str(tmp_path)})()
    first = fleet_key.public_hex(cfg)
    assert oct(os.stat(tmp_path / "fk.json").st_mode & 0o777) == "0o600"
    fleet_key._cache.clear()  # a restart
    assert fleet_key.public_hex(cfg) == first
