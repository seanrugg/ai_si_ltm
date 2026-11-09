"""Enrollment policy, bulk actions and pending expiry (issue #129, server part).

The pending cap is the existing one: ``LTMS_MAX_PENDING_ENROLLMENTS`` default 20.
"""
from __future__ import annotations

import pytest
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from ltms_contract import generate_key, public_key_hex
from server.config import load_config
from server.main import create_app
from server.models import Member
from server.services import enrollment

from .conftest import ADMIN_USER, ADMIN_PASS, AdminClient


@pytest.fixture(autouse=True)
def _reset_refusal_counter():
    """The refusal counter is module-level (one process); reset it per test so
    tests are isolated regardless of order."""
    enrollment.take_refusals()
    yield


def _make_app(db_url, **overrides):
    cfg = load_config(
        database_url=db_url,
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        cookie_secure=False,
        enable_scheduler=False,
        ingest_rate=1000,
        recall_rate=1000,
        enroll_rate_per_ip=1000,
        schema_mode="create",
        **overrides,
    )
    return create_app(config=cfg)


def _db_exec(app, http, stmt):
    """Run a statement or async callable against the app's database."""

    async def go():
        async with app.state.sessionmaker() as session:
            if callable(stmt):
                result = await stmt(session)
            else:
                result = await session.execute(stmt)
            await session.commit()
            return result

    return http.portal.call(go)


def _member_row(app, http, name):
    return _db_exec(app, http, select(Member).where(Member.name == name)).scalar()


def _enroll(http, name, swarm=None, invite=None, permissions=("contribute", "recall")):
    key = generate_key()
    body = {"name": name, "kind": "robot",
            "public_key": public_key_hex(key),
            "permissions": list(permissions)}
    if swarm is not None:
        body["swarm"] = swarm
    if invite is not None:
        body["invite"] = invite
    return http.post("/api/auth/enroll", json=body)


# --------------------------------------------------------------------------- #
# GET /api/admin/enrollment/policy
# --------------------------------------------------------------------------- #
def test_get_policy_default(db_url):
    app = _make_app(db_url)
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200
        g = admin.get("/api/admin/enrollment/policy")
        assert g.status_code == 200, g.text
        body = g.json()
        assert body["policy"] == "manual"
        assert body["source"] == "environment"
        assert body["locked"] is False
        assert body["refusals"] == {"count": 0, "since": None}


# --------------------------------------------------------------------------- #
# manual
# --------------------------------------------------------------------------- #
def test_manual_request_is_pending(db_url):
    app = _make_app(db_url)
    with TestClient(app, base_url="http://testserver") as http:
        r = _enroll(http, "manual-robot")
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "pending"
        assert _member_row(app, http, "manual-robot") is not None


# --------------------------------------------------------------------------- #
# auto_deny
# --------------------------------------------------------------------------- #
def test_auto_deny_refuses_no_member_row(db_url):
    app = _make_app(db_url, enrollment_policy="auto_deny")
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200

        r = _enroll(http, "denied-one")
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "enrollment_closed"
        assert _member_row(app, http, "denied-one") is None

        g = admin.get("/api/admin/enrollment/policy")
        assert g.status_code == 200, g.text
        assert g.json()["refusals"] == {"count": 1, "since": g.json()["refusals"]["since"]}

        r = _enroll(http, "denied-two")
        assert r.status_code == 403
        assert _member_row(app, http, "denied-two") is None
        g = admin.get("/api/admin/enrollment/policy")
        assert g.json()["refusals"]["count"] == 2


def test_auto_deny_invite_swarm_still_succeeds(db_url):
    app = _make_app(db_url, enrollment_policy="auto_deny")
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200

        root = generate_key()
        assert admin.post("/api/admin/swarms",
                          json={"name": "deny-swarm", "public_key": public_key_hex(root)}).status_code == 200
        assert admin.post("/api/admin/swarms/deny-swarm/mode", json={"mode": "invite"}).status_code == 200
        created = admin.post("/api/admin/swarms/deny-swarm/invites", json={"uses": 1})
        assert created.status_code == 200, created.text
        code = created.json()["code"]

        # a swarm in approval mode is still closed under auto_deny (no invite).
        other_root = generate_key()
        assert admin.post("/api/admin/swarms",
                          json={"name": "deny-swarm-approval", "public_key": public_key_hex(other_root)}).status_code == 200
        r = _enroll(http, "denied-in-approval-swarm", swarm="deny-swarm-approval")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "enrollment_closed"

        # a valid invite for the invite-mode swarm still succeeds.
        r = _enroll(http, "invited-one", swarm="deny-swarm", invite=code)
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "active"
        assert _member_row(app, http, "invited-one") is not None


def test_auto_deny_bad_invite_still_403(db_url):
    app = _make_app(db_url, enrollment_policy="auto_deny")
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200
        root = generate_key()
        assert admin.post("/api/admin/swarms",
                          json={"name": "deny-swarm", "public_key": public_key_hex(root)}).status_code == 200
        assert admin.post("/api/admin/swarms/deny-swarm/mode", json={"mode": "invite"}).status_code == 200
        r = _enroll(http, "denied-bad-invite", swarm="deny-swarm", invite="not-a-code")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "bad_invite"
        assert _member_row(app, http, "denied-bad-invite") is None


# --------------------------------------------------------------------------- #
# auto_approve
# --------------------------------------------------------------------------- #
def test_auto_approve_without_confirm_is_422(db_url):
    app = _make_app(db_url, enrollment_policy="auto_approve")
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200
        r = admin.put("/api/admin/enrollment/policy", json={"policy": "auto_approve"})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "confirm_required"


def test_auto_approve_free_agent_recall_only(db_url):
    app = _make_app(db_url, enrollment_policy="auto_approve")
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200
        r = admin.put("/api/admin/enrollment/policy",
                      json={"policy": "auto_approve", "confirm": "auto_approve"})
        assert r.status_code == 200, r.text
        assert r.json()["policy"] == "auto_approve"

        # asked for contribute + recall + policy, gets recall only.
        r = _enroll(http, "auto-one", permissions=["contribute", "recall", "policy"])
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "active"
        row = _member_row(app, http, "auto-one")
        assert row.permissions == ["recall"]
        assert row.requested_permissions == ["contribute", "policy", "recall"]

        # a request naming a swarm keeps that swarm's own mode (pending here).
        swarm_root = generate_key()
        assert admin.post("/api/admin/swarms",
                          json={"name": "aa-swarm", "public_key": public_key_hex(swarm_root)}).status_code == 200
        r = _enroll(http, "auto-swarm", swarm="aa-swarm")
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "pending"


def test_auto_approve_flood_guard_degrades_to_pending(db_url):
    app = _make_app(db_url, enrollment_policy="auto_approve")
    with TestClient(app, base_url="http://testserver") as http:
        for i in range(20):
            r = _enroll(http, f"flood-{i:02d}")
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "active"
        # 21st: over the 20/hour auto-approval flood guard -> pending, not refused.
        r = _enroll(http, "flood-21")
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "pending"


# --------------------------------------------------------------------------- #
# pending cap (existing default 20)
# --------------------------------------------------------------------------- #
def test_pending_cap(db_url):
    app = _make_app(db_url)
    with TestClient(app, base_url="http://testserver") as http:
        for i in range(20):
            r = _enroll(http, f"cap-{i:02d}")
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "pending"
        r = _enroll(http, "cap-21")
        assert r.status_code == 429, r.text
        assert r.json()["error"]["code"] == "too_many_pending"
        assert _member_row(app, http, "cap-21") is None


# --------------------------------------------------------------------------- #
# expiry job
# --------------------------------------------------------------------------- #
def test_expiry_rejects_old_pending(db_url):
    app = _make_app(db_url)
    with TestClient(app, base_url="http://testserver") as http:
        r = _enroll(http, "old-pending")
        assert r.status_code == 202, r.text
        member_id = r.json()["id"]
        _db_exec(app, http, update(Member).where(Member.id == uuid.UUID(member_id))
                 .values(created_at=datetime.now(timezone.utc) - timedelta(days=15)))

        async def go(session):
            return await enrollment.enroll_expire_pending(session, 14)
        res = _db_exec(app, http, go)
        assert res == {"enroll_expire_pending": 1}

        row = _member_row(app, http, "old-pending")
        assert row is not None and row.status == "rejected"


def test_expiry_leaves_recent_pending(db_url):
    app = _make_app(db_url)
    with TestClient(app, base_url="http://testserver") as http:
        r = _enroll(http, "recent-pending")
        assert r.status_code == 202, r.text

        async def go(session):
            return await enrollment.enroll_expire_pending(session, 14)
        res = _db_exec(app, http, go)
        assert res == {"enroll_expire_pending": 0}

        row = _member_row(app, http, "recent-pending")
        assert row is not None and row.status == "pending"


# --------------------------------------------------------------------------- #
# auto_denied scheduler row + counter reset
# --------------------------------------------------------------------------- #
def test_auto_denied_scheduler_row_and_reset(db_url):
    app = _make_app(db_url, enrollment_policy="auto_deny")
    with TestClient(app, base_url="http://testserver") as http:
        for name in ("d1", "d2"):
            r = _enroll(http, name)
            assert r.status_code == 403, r.text

        admin = AdminClient(http)
        assert admin.login().status_code == 200
        g = admin.get("/api/admin/enrollment/policy")
        assert g.json()["refusals"]["count"] == 2

        async def go(session):
            return await enrollment.enroll_auto_denied(session)
        res = _db_exec(app, http, go)
        assert res == {"enroll_auto_denied": 2}

        a = admin.get("/api/admin/recall-log?action=enroll_auto_denied")
        assert a.status_code == 200, a.text
        entries = a.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["detail"]["count"] == 2
        assert entries[0]["detail"]["since"] is not None

        # counter reset after the aggregated row was written.
        g = admin.get("/api/admin/enrollment/policy")
        assert g.json()["refusals"] == {"count": 0, "since": None}


# --------------------------------------------------------------------------- #
# lock
# --------------------------------------------------------------------------- #
def test_locked_policy_put_is_409(db_url):
    app = _make_app(db_url, enrollment_policy="manual", enrollment_policy_locked=True)
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200
        r = admin.put("/api/admin/enrollment/policy",
                      json={"policy": "auto_approve", "confirm": "auto_approve"})
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "locked"


# --------------------------------------------------------------------------- #
# bulk actions
# --------------------------------------------------------------------------- #
def test_bulk_approve_and_reject(db_url):
    app = _make_app(db_url)
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200

        ids = []
        for name in ("b1", "b2", "b3", "b4"):
            r = _enroll(http, name)
            assert r.status_code == 202, r.text
            ids.append(r.json()["id"])

        # b2 is already rejected.
        assert admin.post(f"/api/admin/members/{ids[1]}/reject", json={"reason": "no"}).status_code == 200

        # bulk approve: b1 and b3 ok, b2 (already rejected) -> per-id error.
        r = admin.post("/api/admin/members/bulk", json={"action": "approve", "ids": [ids[0], ids[1], ids[2]]})
        assert r.status_code == 200, r.text
        results = {res["id"]: res for res in r.json()["results"]}
        assert results[ids[0]] == {"id": ids[0], "ok": True}
        assert results[ids[1]] == {"id": ids[1], "error": "bad_state"}
        assert results[ids[2]] == {"id": ids[2], "ok": True}

        # bulk approve never widens requested permissions.
        row = _member_row(app, http, "b1")
        assert row.permissions == row.requested_permissions == ["contribute", "recall"]

        # bulk reject with a reason on the still-pending b4.
        r = admin.post("/api/admin/members/bulk",
                       json={"action": "reject", "ids": [ids[3]], "reason": "bulk no"})
        assert r.status_code == 200, r.text
        assert all(res["ok"] for res in r.json()["results"])

        # rejecting an already-rejected member is a per-id error.
        r = admin.post("/api/admin/members/bulk", json={"action": "reject", "ids": [ids[1]]})
        assert r.status_code == 200, r.text
        assert r.json()["results"][0] == {"id": ids[1], "error": "bad_state"}


def test_bulk_more_than_100_ids_is_422(db_url):
    app = _make_app(db_url)
    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200
        r = admin.post("/api/admin/members/bulk",
                       json={"action": "reject", "ids": ["0"] * 101})
        assert r.status_code == 422, r.text
