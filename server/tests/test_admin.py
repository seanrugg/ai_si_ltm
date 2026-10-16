"""Admin session, CSRF guard, enrollment decisions, dashboard views."""
import pytest

from cli_agent.client import LTMSError
from server.tests.conftest import ADMIN_PASS, ADMIN_USER


def test_admin_requires_login(http):
    for path in ("/api/admin/members", "/api/admin/timeline", "/api/admin/integrity", "/api/admin/me"):
        assert http.get(path).status_code == 401


def test_wrong_password_and_logout(http, admin):
    assert http.post("/api/auth/admin/login", json={"username": ADMIN_USER, "password": "nope"}).status_code == 401
    assert http.post("/api/auth/admin/login", json={"username": "ghost", "password": "x"}).status_code == 401
    assert http.get("/api/admin/me").status_code == 401
    assert admin.get("/api/admin/me").json() == {"username": ADMIN_USER}
    admin.post("/api/auth/admin/logout")
    assert admin.get("/api/admin/me").status_code == 401


def test_cookie_flags(http):
    r = http.post("/api/auth/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    http.cookies.clear()
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api" in cookie


def test_csrf_header_required_for_changes(mesh, admin):
    c = mesh.member("z3-r0", approve=False)
    del admin.headers["x-ltms-admin"]
    r = admin.post(f"/api/admin/members/{c.member_id}/approve", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"


def test_member_token_is_not_admin(mesh, http):
    z3 = mesh.member("z3-r0")
    z3.login()
    assert http.get("/api/admin/members", headers={"Authorization": f"Bearer {z3.token}"}).status_code == 401


def test_enrollment_lifecycle(mesh, admin):
    c = mesh.member("z3-r0", approve=False, permissions=["contribute", "recall"])
    pending = admin.get("/api/admin/members/pending").json()["members"]
    assert [m["name"] for m in pending] == ["z3-r0"]
    # Admin can grant less than requested.
    m = admin.post(f"/api/admin/members/{c.member_id}/approve", json={"permissions": ["contribute"]}).json()
    assert m["status"] == "active" and m["permissions"] == ["contribute"]
    assert admin.post(f"/api/admin/members/{c.member_id}/approve", json={}).status_code == 409
    c.append("event"); c.flush()
    # Revoke: records stay, tokens die, login refused.
    assert admin.post(f"/api/admin/members/{c.member_id}/revoke", json={"reason": "test"}).json()["status"] == "revoked"
    assert c.http.get("/api/records/head", headers={"Authorization": f"Bearer {c.token}"}).status_code == 401
    with pytest.raises(LTMSError):
        c.login()
    assert len(admin.get("/api/admin/timeline", params={"agent": "z3-r0"}).json()["records"]) == 1


def test_reject(mesh, admin):
    c = mesh.member("spam", approve=False)
    assert admin.post(f"/api/admin/members/{c.member_id}/reject", json={"reason": "unknown"}).json()["status"] == "rejected"
    with pytest.raises(LTMSError):
        c.login()


def test_permission_change_kills_old_tokens(mesh, admin):
    c = mesh.member("z3-r0")
    c.login()
    old = c.token
    r = admin.patch(f"/api/admin/members/{c.member_id}/permissions", json={"permissions": ["contribute"]})
    assert r.json()["permissions"] == ["contribute"]
    assert c.http.get("/api/records/head", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    c.token = None
    with pytest.raises(LTMSError) as exc:
        c.records()
    assert exc.value.status == 403
    bad = admin.patch(f"/api/admin/members/{c.member_id}/permissions", json={"permissions": ["root"]})
    assert bad.status_code == 422


def test_views(mesh, admin):
    z3, vec = mesh.member("z3-r0"), mesh.member("vector")
    comm = vec.append("communication", summary="Hello Z3", peers=["z3-r0"]); vec.flush()
    z3.append("communication", summary="Hello Vector", peers=["vector"]); z3.flush()
    dec = z3.append("decision", summary="Approach Vector", refs={"input": f"vector:{comm['seq']}"})
    act = z3.append("action", summary="Approach Vector", place="living-room", outcome="reflex_stop",
                    refs={"decision": f"z3-r0:{dec['seq']}"})
    z3.append("action", summary="Approach Vector", place="living-room", outcome="success",
              refs={"decision": f"z3-r0:{dec['seq']}"})
    follow = z3.append("event", summary="Backed off", refs={"action": f"z3-r0:{act['seq']}"})
    z3.append("lesson", summary="Go slower"); z3.flush()

    conv = admin.get("/api/admin/conversation", params={"member_a": "z3-r0", "member_b": "vector"}).json()
    assert [r["summary"] for r in conv["records"]] == ["Hello Z3", "Hello Vector"]

    trace = admin.get("/api/admin/decision-trace", params={"agent": "z3-r0", "seq": dec["seq"]}).json()
    assert trace["root"]["seq"] == dec["seq"]
    assert [r["agent"] for r in trace["inputs"]] == ["vector"]
    assert sorted(r["outcome"] for r in trace["children"]) == ["reflex_stop", "success"]
    assert [r["seq"] for r in trace["grandchildren"]] == [follow["seq"]]

    out = admin.get("/api/admin/outcomes").json()["groups"]
    assert out[0]["total"] == 2 and out[0]["success_rate"] == 0.5

    assert [r["summary"] for r in admin.get("/api/admin/lessons").json()["lessons"]] == ["Go slower"]
    assert admin.get("/api/admin/records/z3-r0/1").json()["summary"] == "Hello Vector"
    assert admin.get("/api/admin/records/z3-r0/999").status_code == 404
    settings = admin.get("/api/admin/settings").json()
    assert "admin_password" not in settings and settings["max_batch"] == 100
    assert admin.get("/api/admin/timeline", params={"type": "lesson"}).json()["count"] == 1
