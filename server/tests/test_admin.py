"""Admin session, CSRF guard, enrollment decisions, dashboard views."""
import pytest

from cli_agent.client import LTMSError
from ltms_contract import generate_key, public_key_hex
from scripts.swarm_key import appointment, swarm_id
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


def test_swarm_endpoints(mesh, admin):
    root = generate_key()
    pub = public_key_hex(root)
    sid = swarm_id(pub)
    hub = mesh.member("jetson-hub", kind="relay")
    z3 = mesh.member("z3-r0")

    r = admin.post("/api/admin/swarms",
                   json={"name": "echo-base", "public_key": pub, "home": True, "description": "the robot mesh"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == sid and body["home"] is True and body["members"] == []

    listing = admin.get("/api/admin/swarms").json()
    assert [s["name"] for s in listing] == ["echo-base"]
    assert listing[0]["status"] == "active"

    # unknown swarm -> 404
    assert admin.post("/api/admin/swarms/nosuch/members", json={"member": "z3-r0", "role": "member"}).status_code == 404
    assert admin.post("/api/admin/swarms/nosuch/hub", json={}).status_code == 404
    assert admin.post("/api/admin/swarms/nosuch/revoke").status_code == 404

    r = admin.post("/api/admin/swarms/echo-base/members", json={"member": "z3-r0", "role": "member"})
    assert r.status_code == 200, r.text
    assert r.json() == {"swarm": "echo-base", "member": "z3-r0", "role": "member"}
    # bad role -> 422
    assert admin.post("/api/admin/swarms/echo-base/members", json={"member": "z3-r0", "role": "owner"}).status_code == 422

    appt = appointment(root, sid, "jetson-hub", public_key_hex(hub.key))
    r = admin.post("/api/admin/swarms/echo-base/hub", json=appt)
    assert r.status_code == 200, r.text
    assert r.json()["hub"] == "jetson-hub"
    # bad appointment signature -> 422
    bad = appointment(generate_key(), sid, "jetson-hub", public_key_hex(hub.key))
    assert admin.post("/api/admin/swarms/echo-base/hub", json=bad).status_code == 422

    # a relayed record's detail shows its swarm and relay
    rec = z3.append("event", summary="via the hub")
    z3.outbox.clear()
    z3.seq, z3.last_hash = rec["seq"], rec["hash"]
    assert hub.relay([rec])["ok"]
    prov = admin.get("/api/admin/records/z3-r0/1").json()["provenance"]
    assert prov["swarm"] == "echo-base" and prov["relay"] == "jetson-hub"
    assert prov["received_at"]

    # a direct write shows the swarm with no relay
    z3.sync_head()
    rec = z3.append("event", summary="direct")
    z3.flush()
    prov = admin.get("/api/admin/records/z3-r0/2").json()["provenance"]
    assert prov["swarm"] == "echo-base" and prov["relay"] is None

    # remove the member, then revoke the swarm
    r = admin.request("DELETE", "/api/admin/swarms/members/z3-r0")
    assert r.status_code == 200 and r.json()["removed"] is True
    r = admin.post("/api/admin/swarms/echo-base/revoke")
    assert r.status_code == 200 and r.json()["revoked"] is True
    listing = admin.get("/api/admin/swarms").json()
    assert listing[0]["status"] == "revoked"
