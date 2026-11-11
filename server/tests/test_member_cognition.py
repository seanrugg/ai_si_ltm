"""Where a member's AI runs: declared, admin override, resolution (issue #185)."""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from server.models import AuditLog
from server.services.cognition import resolve


def _m(admin=None, observed=None, declared=None):
    return SimpleNamespace(cognition_admin=admin, cognition_observed=observed, cognition_declared=declared)


def test_resolution_order():
    decl = {"location": "onboard", "models": []}
    obs = {"value": "offboard", "offboard_share": 0.73}
    assert resolve(_m()) == {"value": None, "source": None, "offboard_share": None}
    assert resolve(_m(declared=decl)) == {"value": "onboard", "source": "declared", "offboard_share": None}
    assert resolve(_m(observed=obs, declared=decl)) == {"value": "offboard", "source": "observed", "offboard_share": 0.73}
    assert resolve(_m(admin="onboard", observed=obs, declared=decl)) == {
        "value": "onboard", "source": "admin", "offboard_share": 0.73}
    # A malformed or partial observation never wins.
    assert resolve(_m(observed={"offboard_share": 0.1}, declared=decl))["source"] == "declared"


def _member_row(admin, name):
    rows = admin.get("/api/admin/members").json()
    rows = rows.get("members", rows) if isinstance(rows, dict) else rows
    return next(m for m in rows if m["name"] == name)


def _audit(db_exec):
    async def go(session):
        rows = await session.scalars(select(AuditLog).where(AuditLog.action == "cognition_change").order_by(AuditLog.id))
        return [(r.actor, r.detail) for r in rows]
    return db_exec(go)


def test_a_device_declares_and_the_admin_sees_it(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    assert z3.report_cognition("offboard", [{"name": "qwen3", "runs_on": "workstation"}]) == {
        "value": "offboard", "source": "declared", "offboard_share": None}
    assert _member_row(admin, "z3-r0")["cognition"] == {"value": "offboard", "source": "declared", "offboard_share": None}
    # The same report again writes no second audit row.
    z3.report_cognition("offboard", [{"name": "qwen3", "runs_on": "workstation"}])
    rows = _audit(db_exec)
    assert len(rows) == 1
    actor, detail = rows[0]
    assert actor == "z3-r0" and detail["source"] == "declared" and detail["new"] == "offboard"


def test_bad_declarations_are_rejected(mesh):
    z3 = mesh.member("z3-r0")
    z3.login()
    def put(body):
        return z3.http.put("/api/members/me/cognition", json=body, headers={"Authorization": f"Bearer {z3.token}"})
    assert put({"location": "cloud"}).status_code == 422
    assert put({"location": "onboard", "models": [{"name": f"m{i}"} for i in range(5)]}).status_code == 422
    assert put({"location": "onboard", "models": [{"name": "x" * 65}]}).status_code == 422
    r = put({"location": "elsewhere"})
    assert r.json()["error"]["code"] == "bad_cognition"
    # report_cognition logs instead of raising.
    assert z3.report_cognition("cloud") is None


def test_admin_override_then_clear_falls_back_to_the_declaration(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    z3.report_cognition("onboard")
    r = admin.patch(f"/api/admin/members/{z3.member_id}/cognition", json={"cognition": "offboard"})
    assert r.status_code == 200, r.text
    assert r.json()["cognition"]["value"] == "offboard" and r.json()["cognition"]["source"] == "admin"
    r = admin.patch(f"/api/admin/members/{z3.member_id}/cognition", json={"cognition": None})
    assert r.json()["cognition"] == {"value": "onboard", "source": "declared", "offboard_share": None}

    sources = [(d["source"], d["old"], d["new"]) for _, d in _audit(db_exec)]
    assert sources == [("declared", None, "onboard"), ("admin", None, "offboard"), ("admin", "offboard", None)]
    assert _audit(db_exec)[1][0].startswith("admin:")

    assert admin.patch(f"/api/admin/members/{z3.member_id}/cognition", json={"cognition": "cloud"}).status_code == 422
    assert admin.patch(f"/api/admin/members/{z3.member_id}/cognition", json={}).status_code == 422


def test_admin_only(mesh, http):
    z3 = mesh.member("z3-r0")
    r = http.patch(f"/api/admin/members/{z3.member_id}/cognition", json={"cognition": "offboard"},
                   headers={"x-ltms-admin": "1"})
    assert r.status_code == 401
    assert http.put("/api/members/me/cognition", json={"location": "onboard"}).status_code == 401
