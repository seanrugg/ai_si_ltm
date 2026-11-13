"""Invites carry a role; enrollment can say what a member is for (#295)."""
from __future__ import annotations

import pytest

from cli_agent.client import LTMSClient, LTMSError
from ltms_contract import generate_key
from server.tests.test_swarms import Home


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _invite(admin, swarm, **body):
    r = admin.post(f"/api/admin/swarms/{swarm}/invites", json={"uses": 1, **body})
    return r


def _invite_mode(admin, swarm):
    r = admin.post(f"/api/admin/swarms/{swarm}/mode", json={"mode": "invite"})
    assert r.status_code == 200, r.text


def test_invite_with_observer_role_joins_as_observer(http, admin, home):
    _invite_mode(admin, "echo-base")
    r = _invite(admin, "echo-base", role="observer")
    assert r.status_code == 200, r.text and r.json()["role"] == "observer"
    code = r.json()["code"]
    pi = LTMSClient(name="pi-zero", signing_key=generate_key(), http=http)
    res = pi._call("POST", "/api/auth/enroll", auth=False, json={
        "name": "pi-zero", "kind": "robot", "public_key": pi.key.verify_key.encode().hex(),
        "permissions": ["contribute"], "swarm": "echo-base", "invite": code})
    assert res["status"] == "active"
    swarms = admin.get("/api/admin/swarms").json()
    roles = {m["name"]: m["role"] for s in swarms for m in s["members"]}
    assert roles["pi-zero"] == "observer"


def test_invite_role_defaults_to_member_and_refuses_hub(admin, home):
    _invite_mode(admin, "echo-base")
    r = _invite(admin, "echo-base")
    assert r.status_code == 200 and r.json()["role"] == "member"
    r = _invite(admin, "echo-base", role="hub")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_role"
    assert "root key" in r.json()["error"]["message"]


def test_intended_is_stored_shown_and_grants_nothing(http, admin, home, db_exec):
    hub = LTMSClient(name="hub-two", signing_key=generate_key(), http=http)
    res = hub.enroll("relay", ["contribute"], intended={"swarm": "echo-base", "role": "hub"})
    assert res["status"] == "pending"
    m = next(x for x in admin.get("/api/admin/members").json()["members"] if x["name"] == "hub-two")
    assert m["intended"] == {"swarm": "echo-base", "role": "hub"}
    assert m["permissions"] == [] and m["status"] == "pending"
    admin.post(f"/api/admin/members/{m['id']}/approve", json={})
    m = next(x for x in admin.get("/api/admin/members").json()["members"] if x["name"] == "hub-two")
    assert "relay" not in m["permissions"]
    swarms = admin.get("/api/admin/swarms").json()
    assert all(mm["name"] != "hub-two" for s in swarms for mm in s["members"])  # not joined by itself
    from sqlalchemy import select
    from server.models import AuditLog

    async def go(s):
        return [a.detail for a in await s.scalars(select(AuditLog).where(AuditLog.action == "enroll_request"))]
    assert any((d or {}).get("intended") == {"swarm": "echo-base", "role": "hub"} for d in db_exec(go))


@pytest.mark.parametrize("intended", [{"swarm": "echo-base", "role": "boss"}, {"swarm": "Bad Name!"},
                                      {"swarm": "echo-base", "extra": 1}, "hub"])
def test_bad_intended_is_refused(http, intended):
    c = LTMSClient(name="odd-one", signing_key=generate_key(), http=http)
    with pytest.raises(LTMSError) as exc:
        c.enroll("robot", ["contribute"], intended=intended)
    assert exc.value.code in ("bad_intended", "invalid_request")  # a non-object fails request validation
