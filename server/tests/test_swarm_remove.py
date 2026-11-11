"""Removing a swarm (issue #232)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from ltms_contract import public_key_hex
from server.models import AuditLog, Member, Record, Swarm
from server.tests.test_swarms import Home, sign


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _remove(admin, name, members="free", records="keep", confirm=None):
    return admin.post(f"/api/admin/swarms/{name}/remove",
                      json={"confirm": name if confirm is None else confirm, "members": members, "records": records})


def _member(db_exec, name):
    async def go(session):
        return await session.scalar(select(Member).where(Member.name == name))
    return db_exec(go)


def _swarm(db_exec, swarm_id):
    async def go(session):
        return await session.get(Swarm, swarm_id)
    return db_exec(go)


def _setup(mesh, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    hub.relay(sign(z3, 2))
    return hub, z3


def test_free_members_hide_swarm_keep_records(mesh, admin, db_exec, home):
    hub, z3 = _setup(mesh, home)
    r = _remove(admin, "echo-base")
    assert r.status_code == 200, r.text
    assert r.json() == {"id": home.id, "name": "echo-base", "status": "removed", "members": "free",
                        "released": 2, "revoked": 0, "records": "keep", "purged": 0}
    assert "echo-base" not in [s["name"] for s in admin.get("/api/admin/swarms").json()]
    for name in ("z3-r0", "jetson-hub"):
        m = _member(db_exec, name)
        assert m.status == "active" and "relay" not in m.permissions
    row = _swarm(db_exec, home.id)
    assert (row.status, row.former_name, row.name) == ("removed", "echo-base", f"echo-base.removed.{home.id[:8]}")

    async def records(session):
        return len(list(await session.scalars(select(Record).where(Record.agent == "z3-r0"))))
    assert db_exec(records) == 2

    async def audit(session):
        return [r.detail for r in await session.scalars(select(AuditLog).where(AuditLog.action == "swarm_remove"))]
    assert db_exec(audit)[0]["count"] == 2


def test_remove_members_revokes_them(mesh, admin, db_exec, home):
    _setup(mesh, home)
    r = _remove(admin, "echo-base", members="remove")
    assert r.json()["revoked"] == 2
    for name in ("z3-r0", "jetson-hub"):
        assert _member(db_exec, name).status == "revoked"

    async def reasons(session):
        return {r.detail.get("reason") for r in await session.scalars(
            select(AuditLog).where(AuditLog.action == "member_revoke"))}
    assert db_exec(reasons) == {"swarm echo-base removed"}


def test_name_reusable_but_not_the_key(mesh, admin, db_exec, home):
    _remove(admin, "echo-base")
    again = admin.post("/api/admin/swarms", json={"name": "echo-base", "public_key": public_key_hex(home.root)})
    assert again.status_code == 409 and "removed swarm used this key" in again.json()["error"]["message"]
    other = Home(mesh, db_exec, name="echo-base")  # a new key may take the name
    assert other.id != home.id
    assert [s["name"] for s in admin.get("/api/admin/swarms").json()] == ["echo-base"]


def test_validation(mesh, admin, http, home):
    assert _remove(admin, "echo-base", confirm="nope").json()["error"]["code"] == "bad_confirm"
    assert _remove(admin, "echo-base", members="explode").json()["error"]["code"] == "bad_members"
    assert _remove(admin, "echo-base", records="shred").json()["error"]["code"] == "bad_records"
    assert _remove(admin, "echo-base").status_code == 200
    assert _remove(admin, "echo-base").status_code == 404
    r = http.post("/api/admin/swarms/echo-base/remove", json={}, headers={"x-ltms-admin": "1"})
    assert r.status_code == 401
