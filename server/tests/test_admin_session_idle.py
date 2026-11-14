"""Admin sessions: idle timeout, maximum length, activity and settings (issue #378)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from server.models import AdminSession, AuditLog
from server.tests.conftest import AdminClient

ACTIVE = {"x-ltms-activity": "user"}


def _now():
    return datetime.now(timezone.utc)


def _shift(db_exec, *, issued_ago=None, active_ago=None):
    """Move the (only) admin session's clock back instead of sleeping."""
    values = {}
    if issued_ago is not None:
        values["issued_at"] = _now() - issued_ago
    if active_ago is not None:
        values["last_active_at"] = _now() - active_ago
    db_exec(update(AdminSession).values(**values))


def _last_active(db_exec):
    async def go(session):
        return (await session.scalars(select(AdminSession))).one().last_active_at
    return db_exec(go)


def test_defaults_are_60_minutes_idle_and_12_hours(admin):
    r = admin.get("/api/admin/sessions/settings")
    assert r.status_code == 200
    body = r.json()
    assert (body["idle_minutes"], body["max_hours"], body["warn_minutes"]) == (60, 12, 2)
    assert body["source"] == "environment"


def test_login_cookie_covers_the_maximum(http):
    r = http.post("/api/auth/admin/login", json={"username": "admin", "password": "correct horse battery staple"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "Max-Age=86400" in cookie
    assert r.json()["session"]["idle_remaining_seconds"] == 3600
    http.cookies.clear()


def test_session_used_every_50_minutes_stays_valid(admin, db_exec):
    # Past the old fixed 30 minutes...
    _shift(db_exec, issued_ago=timedelta(minutes=50), active_ago=timedelta(minutes=50))
    assert admin.get("/api/admin/me", headers=ACTIVE).status_code == 200
    # ...and past 2 hours, with activity every 50 minutes.
    _shift(db_exec, issued_ago=timedelta(hours=2, minutes=10), active_ago=timedelta(minutes=50))
    r = admin.get("/api/admin/me", headers=ACTIVE)
    assert r.status_code == 200
    assert int(r.headers["x-ltms-session-idle-remaining"]) > 59 * 60


def test_idle_61_minutes_is_rejected(admin, db_exec):
    _shift(db_exec, active_ago=timedelta(minutes=61))
    r = admin.get("/api/admin/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_logged_in"
    assert r.json()["error"]["message"] == "admin session expired (inactivity)"


def test_past_maximum_is_rejected_even_if_active(admin, db_exec):
    _shift(db_exec, issued_ago=timedelta(hours=12, minutes=1), active_ago=timedelta(seconds=5))
    r = admin.get("/api/admin/me", headers=ACTIVE)
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "admin session expired (maximum length)"


def test_polling_does_not_move_last_active(admin, db_exec):
    _shift(db_exec, active_ago=timedelta(minutes=10))
    before = _last_active(db_exec)
    assert admin.get("/api/admin/me").status_code == 200
    assert _last_active(db_exec) == before
    r = admin.get("/api/admin/me")
    assert int(r.headers["x-ltms-session-idle-remaining"]) <= 50 * 60


def test_user_request_moves_last_active(admin, db_exec):
    _shift(db_exec, active_ago=timedelta(minutes=10))
    before = _last_active(db_exec)
    assert admin.get("/api/admin/me", headers=ACTIVE).status_code == 200
    assert _last_active(db_exec) > before


def test_activity_endpoint_moves_last_active_and_is_throttled(admin, db_exec):
    _shift(db_exec, active_ago=timedelta(minutes=10))
    before = _last_active(db_exec)
    r = admin.post("/api/auth/admin/activity")
    assert r.status_code == 200
    first = _last_active(db_exec)
    assert first > before
    assert r.json()["session"]["idle_remaining_seconds"] >= 59 * 60
    # A second heartbeat within the minute writes nothing.
    assert admin.post("/api/auth/admin/activity").status_code == 200
    assert _last_active(db_exec) == first


def test_activity_endpoint_needs_csrf_header(admin):
    r = admin.post("/api/auth/admin/activity", headers={"x-ltms-admin": "0"})
    assert r.status_code == 403


def test_activity_endpoint_cannot_revive_an_idle_session(admin, db_exec):
    _shift(db_exec, active_ago=timedelta(minutes=61))
    assert admin.post("/api/auth/admin/activity").status_code == 401


@pytest.mark.parametrize("body", [
    {"idle_minutes": 14, "max_hours": 12, "warn_minutes": 2},
    {"idle_minutes": 481, "max_hours": 12, "warn_minutes": 2},
    {"idle_minutes": 60, "max_hours": 0, "warn_minutes": 2},
    {"idle_minutes": 60, "max_hours": 25, "warn_minutes": 2},
    {"idle_minutes": 60, "max_hours": 12, "warn_minutes": 0},
    {"idle_minutes": 60, "max_hours": 12, "warn_minutes": 11},
    {"idle_minutes": 180, "max_hours": 2, "warn_minutes": 2},      # maximum < idle
    {"idle_minutes": "60", "max_hours": 12, "warn_minutes": 2},
    {"idle_minutes": 60, "max_hours": 12},
])
def test_settings_ranges_are_validated(admin, body):
    r = admin.put("/api/admin/sessions/settings", json=body)
    assert r.status_code == 422, body
    assert r.json()["error"]["code"] == "bad_admin_sessions"


def test_settings_change_is_audited_and_applies_to_open_sessions(admin, db_exec):
    _shift(db_exec, active_ago=timedelta(minutes=20))
    r = admin.put("/api/admin/sessions/settings", json={"idle_minutes": 15, "max_hours": 8, "warn_minutes": 1})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "dashboard"

    async def audit(session):
        return (await session.scalars(select(AuditLog).where(AuditLog.action == "settings_change"))).all()
    rows = db_exec(audit)
    assert len(rows) == 1
    assert rows[0].detail["old"]["idle_minutes"] == 60
    assert rows[0].detail["new"]["idle_minutes"] == 15
    assert rows[0].detail["setting"] == "admin_sessions"

    # The PUT itself carried no activity header; 20 minutes idle now exceeds 15.
    r = admin.get("/api/admin/me")
    assert r.status_code == 401
    assert "inactivity" in r.json()["error"]["message"]


def test_longer_maximum_applies_to_open_sessions(admin, db_exec):
    assert admin.put("/api/admin/sessions/settings",
                     json={"idle_minutes": 60, "max_hours": 20, "warn_minutes": 2}).status_code == 200
    _shift(db_exec, issued_ago=timedelta(hours=13), active_ago=timedelta(minutes=1))
    assert admin.get("/api/admin/me").status_code == 200


@pytest.fixture
def capped_config(config):
    return config.model_copy(update={"admin_idle_max_minutes": 30, "admin_idle_minutes": 60})


@pytest.fixture
def capped_admin(capped_config):
    from fastapi.testclient import TestClient

    from server.main import create_app
    with TestClient(create_app(capped_config), base_url="http://testserver") as http:
        client = AdminClient(http)
        assert client.login().status_code == 200
        yield client


def test_env_ceiling_caps_the_idle_timeout(capped_admin):
    r = capped_admin.get("/api/admin/sessions/settings")
    body = r.json()
    assert body["limits"]["idle_minutes_max"] == 30
    assert body["idle_minutes"] == 30  # the env default of 60 is held to the ceiling
    r = capped_admin.put("/api/admin/sessions/settings", json={"idle_minutes": 45, "max_hours": 12, "warn_minutes": 2})
    assert r.status_code == 422
    r = capped_admin.put("/api/admin/sessions/settings", json={"idle_minutes": 30, "max_hours": 12, "warn_minutes": 2})
    assert r.status_code == 200


def test_member_token_lifetime_is_unchanged(config):
    assert config.token_ttl_seconds == 300
