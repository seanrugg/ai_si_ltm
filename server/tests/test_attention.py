"""Overview's "Needs attention" (#380): each kind appears and clears, silence by
interval with Ping, per-admin snoozes, counts that match the nav badges."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from cli_agent.directives import Decision, DirectiveHandler
from server.models import AdminUser, DatasetVersion, LrsEndpoint
from server.services import attention
from server.tests.test_charter import _age_enrollment, _publish, _quiet, V1
from server.tests.test_datasets_read import _dataset, data_dir  # noqa: F401
from server.tests.test_directive_acks import Link, _wire, fleet_key_file  # noqa: F401
from server.tests.test_fleet_health import _beats
from server.tests.test_integrity_incremental import run_full_check
from server.tests.test_swarms import Home


def _view(admin, **params):
    r = admin.get("/api/admin/attention", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _kinds(body):
    return [i["kind"] for i in body["items"]]


def _by_key(body):
    return {i["key"]: i for i in body["items"]}


def _service(app, db_exec, *, now=None, admin_id=None):
    async def go(session):
        aid = admin_id or (await session.scalars(select(AdminUser.id))).first()
        return await attention.view(session, app.state, app.state.config, aid, now=now)
    return db_exec(go)


def test_empty_fleet_and_admin_only(admin, http):
    body = _view(admin)
    assert body["items"] == [] and body["snoozed"] == []
    assert datetime.fromisoformat(body["checked_at"]).tzinfo is not None
    assert body["counts"] == {"members_pending": 0, "charter_overdue": 0, "directive_declines": 0, "lrs_failing": 0}
    http.cookies.clear()
    assert http.get("/api/admin/attention").status_code == 401


def test_enrollments_and_lrs_appear_then_clear_and_match_badges(admin, mesh, db_exec):
    pending = mesh.member("pending-one", approve=False)

    async def add_lrs(session):
        session.add(LrsEndpoint(name="bad", url="https://b.example.org", enabled=True, last_error="HTTP 500"))
    db_exec(add_lrs)

    body = _view(admin)
    items = _by_key(body)
    assert items["enrollments"]["count"] == 1 and items["enrollments"]["link"] == "/members"
    assert items["enrollments"]["severity"] == "action" and items["enrollments"]["since"]
    assert items["lrs"]["count"] == 1 and items["lrs"]["link"] == "/lrs"
    nav = admin.get("/api/admin/nav-status").json()
    assert body["counts"] == {k: nav[k] for k in body["counts"]}

    assert admin.post(f"/api/admin/members/{pending.member_id}/approve", json={"scope": None}).status_code == 200

    async def fix_lrs(session):
        for e in await session.scalars(select(LrsEndpoint)):
            e.last_error = None
    db_exec(fix_lrs)
    assert _view(admin)["items"] == []


def test_charter_overdue_appears(admin, mesh, db_exec, data_dir):  # noqa: F811
    _quiet(mesh.member("late-one"))
    _age_enrollment(db_exec, "late-one", 30)
    _publish(admin, V1)

    async def backdate(session):
        for v in await session.scalars(select(DatasetVersion)):
            v.approved_at = datetime.now(timezone.utc) - timedelta(days=20)
    db_exec(backdate)
    item = _by_key(_view(admin))["charter_overdue"]
    assert item["count"] == 1 and item["link"] == "/charter" and "paused" in item["title"]


def test_directive_declines_with_reasons_and_not_received(app, mesh, admin, http, db_exec, tmp_path):
    home = Home(mesh, db_exec)
    vec, other = mesh.member("vector"), mesh.member("other")
    home.add(vec)
    home.add(other)
    pub = http.get("/api/fleet-key").json()["public_key"]
    d = admin.post("/api/admin/directives", json={"scope": {"swarm": "echo-base"}, "text": "Dock"}).json()
    no = DirectiveHandler(Link(vec), lambda: pub, str(tmp_path / "v.json"),
                          decide=lambda stmt: Decision.decline("outside my safety envelope"))
    assert no.on_message(_wire(d)) == "declined"

    body = _view(admin)
    item = _by_key(body)["directive_declines"]
    assert item["count"] == 1 == body["counts"]["directive_declines"]
    assert item["reasons"] == ["vector: outside my safety envelope"]
    assert "directive_unreceived:" + d["id"] not in _by_key(body)  # within the grace hour

    later = datetime.now(timezone.utc) + timedelta(hours=2)
    unreceived = _by_key(_service(app, db_exec, now=later))
    item = unreceived["directive_unreceived:" + d["id"]]
    assert item["count"] == 1 and item["severity"] == "info" and "Dock" in item["title"]

    seen = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    assert "directive_declines" not in _by_key(_view(admin, directives_seen=seen))


def test_silent_members_by_interval_with_ping(app, mesh, db_exec):
    fast, slow = mesh.member("fast"), mesh.member("slow")
    _beats(fast, 60)
    _beats(slow, 600)
    first = _service(app, db_exec)
    assert "silent:fast" not in _by_key(first)
    heard = datetime.now(timezone.utc)
    items = _by_key(_service(app, db_exec, now=heard + timedelta(seconds=300)))
    assert items["silent:fast"]["ping"] is True and items["silent:fast"]["subject"] == "fast"
    assert items["silent:fast"]["link"] == "/members/fast/profile?tab=senses"
    assert "silent:slow" not in items  # 300 s is within 2 × 600 s


def test_silence_without_an_interval_uses_24h(app, mesh, db_exec):
    quiet = mesh.member("quiet")
    quiet.append("action", summary="did a thing", outcome="success")
    quiet.flush()
    now = datetime.now(timezone.utc)
    assert "silent:quiet" not in _by_key(_service(app, db_exec, now=now + timedelta(hours=47)))
    item = _by_key(_service(app, db_exec, now=now + timedelta(hours=49)))["silent:quiet"]
    assert "24 h" in item["title"]


def test_hub_health_warning_appears(mesh, admin, db_exec):
    home = Home(mesh, db_exec, name="alpha")
    hub, calm = mesh.member("hub-a", kind="relay"), mesh.member("hub-b", kind="relay")
    home.appoint(hub)
    hub.append("perception", summary="machine health", tags=["telemetry", "internal", "health"],
               payload={"sensor": "jetson-health", "services": {"wire-pod": "failed"}, "throttled": True})
    hub.append("event", summary="throttle: gpu 86 C", tags=["internal", "health", "throttle"],
               payload={"sensor": "jetson-health"})
    hub.flush()
    calm.append("perception", summary="machine health", tags=["telemetry", "internal", "health"],
                payload={"sensor": "jetson-health", "services": {"wire-pod": "active"}, "throttled": False})
    calm.flush()
    items = _by_key(_view(admin))
    item = items["health:hub-a"]
    assert item["title"] == "hub-a health: 1 throttle event in 24 h, wire-pod failed"
    assert item["severity"] == "action" and item["link"] == "/members/hub-a/profile?tab=senses"
    assert "health:hub-b" not in items


def test_integrity_break_appears(app, mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    for i in range(3):
        z3.append("action", summary=f"a{i}", outcome="success")
    z3.flush()
    assert "integrity" not in _by_key(_view(admin))
    if app.state.engine.dialect.name == "postgresql":
        db_exec(text("ALTER TABLE records DISABLE TRIGGER trg_records_no_mutation"))
    else:
        db_exec(text("DROP TRIGGER trg_records_no_update"))
    db_exec(text("UPDATE records SET outcome = 'failed' WHERE seq = 2"))
    assert "integrity" not in _by_key(_view(admin))  # the cached check is under an hour old
    # An edit behind an existing checkpoint needs a full check (#439), which refreshes it.
    assert run_full_check(admin)["status"] == "done"
    item = _by_key(_view(admin))["integrity"]
    assert item["count"] == 1 and "z3-r0" in item["title"] and item["link"] == "/integrity"


def test_pending_dataset_versions_appear(admin, data_dir):  # noqa: F811
    _dataset(admin, approve=False)
    item = _by_key(_view(admin))["dataset_versions"]
    assert item["count"] == 1 and item["severity"] == "info" and item["link"] == "/datasets"


def test_sorted_by_severity_then_age(admin, mesh, data_dir):  # noqa: F811
    _dataset(admin, approve=False)
    mesh.member("pending-one", approve=False)
    assert _kinds(_view(admin)) == ["enrollments", "dataset_versions"]


def test_snooze_hides_for_24h_for_that_admin_only(app, admin, mesh, db_exec):
    mesh.member("pending-one", approve=False)
    r = admin.post("/api/admin/attention/snooze", json={"key": "enrollments"})
    assert r.status_code == 200, r.text
    body = _view(admin)
    assert "enrollments" not in _by_key(body)
    assert [s["key"] for s in body["snoozed"]] == ["enrollments"]
    assert body["counts"]["members_pending"] == 1  # snoozing doesn't resolve it

    async def other_admin(session):
        u = AdminUser(username="second-admin", password_hash="x")
        session.add(u)
        await session.flush()
        return u.id
    other = db_exec(other_admin)
    assert "enrollments" in _by_key(_service(app, db_exec, admin_id=other))

    tomorrow = datetime.now(timezone.utc) + timedelta(hours=24, minutes=1)
    assert "enrollments" in _by_key(_service(app, db_exec, now=tomorrow))

    assert admin.delete("/api/admin/attention/snooze/enrollments").status_code == 200
    assert "enrollments" in _by_key(_view(admin))
    assert admin.post("/api/admin/attention/snooze", json={"key": ""}).status_code == 422
