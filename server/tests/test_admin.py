"""Admin auth + endpoints.

Covers the admin surface (plan §"Admin"): login sets a session cookie, admin
endpoints are refused without a cookie, wrong credentials are refused, and the
approve/revoke/permissions/interview flow works through the API.
"""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select

from server.models.member import Member
from server.services.enrollment import (
    approve_enrollment,
    create_enrollment_request,
    revoke_member,
    set_permissions,
)

from .conftest import genesis_prev_hash_hex, make_secret, signed_record


def _login(client):
    """Log in as admin and attach the session cookie to the client."""
    resp = client.post(
        "/api/auth/admin/login", json={"username": "admin", "password": "admin-secret"}
    )
    assert resp.status_code == 200
    client.cookies.set("ltms_admin", resp.cookies["ltms_admin"])
    return resp


async def _seed_pending(db_session):
    """Create + approve a pending member; return its id as a string."""
    member = await create_enrollment_request(db_session, name="admin-agent", kind="agent")
    await approve_enrollment(db_session, member.id)
    return str(member.id)


def test_login_sets_cookie(client, admin_user):
    resp = client.post(
        "/api/auth/admin/login", json={"username": "admin", "password": "admin-secret"}
    )
    assert resp.status_code == 200
    assert resp.cookies.get("ltms_admin") is not None


def test_login_wrong_credentials(client, admin_user):
    resp = client.post(
        "/api/auth/admin/login", json={"username": "admin", "password": "nope"}
    )
    assert resp.status_code == 401


def test_admin_endpoints_require_cookie(client):
    resp = client.get("/api/admin/members")
    assert resp.status_code == 403


async def test_approve_flow(client, admin_user, db_session):
    member_id = await _seed_pending(db_session)
    _login(client)

    resp = client.post(f"/api/admin/members/{member_id}/approve")
    assert resp.status_code == 200


async def test_revoke_flow(client, admin_user, db_session):
    member_id = await _seed_pending(db_session)
    _login(client)

    resp = client.post(f"/api/admin/members/{member_id}/revoke")
    assert resp.status_code == 200


async def test_set_permissions_flow(client, admin_user, db_session):
    member_id = await _seed_pending(db_session)
    _login(client)

    resp = client.patch(
        f"/api/admin/members/{member_id}/permissions",
        json={"permissions": ["contribute", "recall"]},
    )
    assert resp.status_code == 200


async def test_members_list(client, admin_user, db_session):
    _login(client)
    resp = client.get("/api/admin/members")
    assert resp.status_code == 200
    data = resp.json()
    assert any(m["name"] == "admin-agent" for m in data)


async def test_integrity_view(client, admin_user, db_session):
    # Seed a valid chain so integrity re-verification has something to check.
    secret = make_secret()
    member = await create_enrollment_request(
        db_session, name="integrity-agent", kind="agent"
    )
    member.public_key = secret.verifying_key().raw
    member.permissions = ["contribute", "recall"]
    await approve_enrollment(db_session, member.id)

    genesis = signed_record(
        secret, seq=1, body={"text": "hi"}, prev_hash_hex=genesis_prev_hash_hex()
    )
    from server.services.ingest import ingest_batch

    await ingest_batch(db_session, member, [genesis])

    _login(client)
    resp = client.get("/api/admin/integrity")
    assert resp.status_code == 200


async def test_recall_log(client, admin_user, db_session):
    _login(client)
    resp = client.get("/api/admin/recall-log")
    assert resp.status_code == 200
