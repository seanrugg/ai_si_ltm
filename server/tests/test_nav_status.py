"""GET /api/admin/nav-status: badge counts and Get started progress (#379)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from cli_agent.directives import Decision, DirectiveHandler
from server.models import AdminSession, DatasetVersion, LrsEndpoint
from server.tests.test_charter import _age_enrollment, _publish, _quiet, V1
from server.tests.test_datasets_read import data_dir  # noqa: F401
from server.tests.test_directive_acks import Link, _wire, fleet_key_file  # noqa: F401
from server.tests.test_swarms import Home

EMPTY_SETUP = {"charter_published": False, "swarm_created": False, "hub_appointed": False,
               "lrs_added": False, "member_approved": False}


def test_empty_fleet(admin):
    r = admin.get("/api/admin/nav-status")
    assert r.status_code == 200
    body = r.json()
    assert datetime.fromisoformat(body.pop("as_of")).tzinfo is not None
    assert body == {"members_pending": 0, "charter_overdue": 0, "directive_declines": 0,
                        "lrs_failing": 0, "setup": EMPTY_SETUP}


def test_requires_admin(http):
    assert http.get("/api/admin/nav-status").status_code == 401


def test_setup_ticks_from_server_state(admin, mesh, db_exec, data_dir):  # noqa: F811
    mesh.member("pending-one", approve=False)
    hub = mesh.member("hub-one")
    home = Home(mesh, db_exec, name="test-swarm")
    home.add(hub)
    home.appoint(hub)
    _publish(admin, V1)

    async def add_lrs(session):
        session.add(LrsEndpoint(name="lrs-one", url="https://lrs.example.org", enabled=False))
    db_exec(add_lrs)

    body = admin.get("/api/admin/nav-status").json()
    assert body["setup"] == {k: True for k in EMPTY_SETUP}
    assert body["members_pending"] == 1
    assert body["lrs_failing"] == 1  # paused counts


def test_lrs_failing_counts_errors_not_healthy(admin, db_exec):
    async def add(session):
        session.add(LrsEndpoint(name="ok", url="https://a.example.org", enabled=True))
        session.add(LrsEndpoint(name="bad", url="https://b.example.org", enabled=True, last_error="HTTP 500"))
    db_exec(add)
    assert admin.get("/api/admin/nav-status").json()["lrs_failing"] == 1


def test_charter_overdue(admin, mesh, db_exec, data_dir):  # noqa: F811
    _quiet(mesh.member("late-one"))
    reader = _quiet(mesh.member("reader-one"))
    _age_enrollment(db_exec, "late-one", 30)
    _age_enrollment(db_exec, "reader-one", 30)
    _publish(admin, V1)
    reader.read_charter()
    assert admin.get("/api/admin/nav-status").json()["charter_overdue"] == 0  # within the grace period

    async def backdate(session):  # published 20 days ago: past the 14-day grace period
        for v in await session.scalars(select(DatasetVersion)):
            v.approved_at = datetime.now(timezone.utc) - timedelta(days=20)
    db_exec(backdate)
    assert admin.get("/api/admin/nav-status").json()["charter_overdue"] == 1


def test_directive_declines_since_last_look(mesh, admin, http, db_exec, tmp_path):
    home = Home(mesh, db_exec)
    vec = mesh.member("vector")
    home.add(vec)
    pub = http.get("/api/fleet-key").json()["public_key"]
    d = admin.post("/api/admin/directives", json={"scope": {"swarm": "echo-base"}, "text": "Dock"}).json()
    no = DirectiveHandler(Link(vec), lambda: pub, str(tmp_path / "v.json"),
                          decide=lambda stmt: Decision.decline("outside my safety envelope"))
    assert no.on_message(_wire(d)) == "declined"

    assert admin.get("/api/admin/nav-status").json()["directive_declines"] == 1
    later = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    r = admin.get("/api/admin/nav-status", params={"directives_seen": later})
    assert r.json()["directive_declines"] == 0
    assert admin.get("/api/admin/nav-status", params={"directives_seen": "nope"}).status_code == 422


def test_polling_is_not_admin_activity(admin, db_exec):
    async def last(session):
        return (await session.scalars(select(AdminSession))).one().last_active_at

    async def age(session):
        row = (await session.scalars(select(AdminSession))).one()
        row.last_active_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_exec(age)
    before = db_exec(last)
    assert admin.get("/api/admin/nav-status").status_code == 200
    assert db_exec(last) == before
