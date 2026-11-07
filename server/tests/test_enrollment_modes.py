"""Per-swarm enrollment modes: approval, invite, open (issue #109).

Runs against PostgreSQL (LTMS_TEST_DATABASE_URL); the db_url fixture from
conftest.py gives a fresh database per test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from cli_agent.client import LTMSClient
from ltms_contract import generate_key, public_key_hex
from server.models import Invite, Member, Swarm, SwarmMember
from server.tests.test_swarms import Home, sign


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _mid(member: dict) -> uuid.UUID:
    return uuid.UUID(member["id"])


def _member_row(db_exec, name: str) -> dict | None:
    return db_exec(lambda s: s.execute(
        select(Member).where(Member.name == name))).scalar()


def _membership(db_exec, member_id: uuid.UUID, swarm_id: str) -> SwarmMember | None:
    return db_exec(lambda s: s.execute(
        select(SwarmMember).where(
            SwarmMember.member_id == member_id,
            SwarmMember.swarm_id == swarm_id,
            SwarmMember.left_at.is_(None)))).scalar()


def _invite_row(db_exec, swarm_id: str) -> Invite | None:
    return db_exec(lambda s: s.execute(
        select(Invite).where(Invite.swarm_id == swarm_id))).scalar()


def _enroll(http, name, swarm=None, invite=None, permissions=("contribute", "recall")):
    client = LTMSClient(name=name, signing_key=generate_key(), http=http)
    body = {"name": name, "kind": "robot",
            "public_key": public_key_hex(client.key),
            "permissions": list(permissions)}
    if swarm is not None:
        body["swarm"] = swarm
    if invite is not None:
        body["invite"] = invite
    return client, http.post("/api/auth/enroll", json=body)


# ---------------------------------------------------------------- approval

def test_approval_mode_stays_pending_until_approved(mesh, admin, db_exec, home, http):
    # echo-base is in approval mode by default.
    client, r = _enroll(http, "z3-approve", swarm="echo-base")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending"
    assert _member_row(db_exec, "z3-approve") is not None

    m = admin.post(f"/api/admin/members/{_mid(r.json())}/approve", json={}).json()
    assert m["status"] == "active"
    row = _membership(db_exec, _mid(m), home.id)
    assert row is not None and row.role == "member"

    # Approved, so it can write its own records.
    client.login()
    client.append("event", summary="joined the swarm")
    assert client.flush()[0]["ok"] is True


# ---------------------------------------------------------------- invite

def test_invite_mode_happy_path(mesh, admin, db_exec, home, http):
    admin.post("/api/admin/swarms/echo-base/mode", json={"mode": "invite"})

    created = admin.post("/api/admin/swarms/echo-base/invites", json={"uses": 2})
    assert created.status_code == 200, created.text
    code = created.json()["code"]
    assert created.json()["uses_left"] == 2

    # It asked for policy too, but an invitee only ever gets contribute/recall.
    client, r = _enroll(http, "z3-invite", swarm="echo-base", invite=code,
                        permissions=["contribute", "recall", "policy"])
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "active"

    client.login()
    assert client.permissions == ["contribute", "recall"]

    m = _member_row(db_exec, "z3-invite")
    assert m is not None and m.note is None
    row = _membership(db_exec, m.id, home.id)
    assert row is not None and row.role == "member"

    invite_row = _invite_row(db_exec, home.id)
    assert invite_row is not None and invite_row.uses_left == 1

    # The same code still has one use left.
    client2, r2 = _enroll(http, "z3-invite-two", swarm="echo-base", invite=code)
    assert r2.status_code == 202, r2.text
    assert _invite_row(db_exec, home.id).uses_left == 0

    # It can append its own records, but was never granted relay.
    client.append("event", summary="first record")
    assert client.flush()[0]["ok"] is True
    assert "relay" not in client.permissions


def test_invite_missing_invalid_exhausted_expired_403(mesh, admin, db_exec, home, http):
    admin.post("/api/admin/swarms/echo-base/mode", json={"mode": "invite"})

    _, r = _enroll(http, "z3-no-code", swarm="echo-base")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "bad_invite"

    _, r = _enroll(http, "z3-bad-code", swarm="echo-base", invite="not-a-code")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "bad_invite"

    # No members were created for the rejected attempts.
    assert _member_row(db_exec, "z3-no-code") is None
    assert _member_row(db_exec, "z3-bad-code") is None

    created = admin.post("/api/admin/swarms/echo-base/invites", json={"uses": 1})
    code = created.json()["code"]
    _, r = _enroll(http, "z3-first", swarm="echo-base", invite=code)
    assert r.status_code == 202, r.text

    _, r = _enroll(http, "z3-second", swarm="echo-base", invite=code)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "bad_invite"

    # An invite that has since expired. Creating one already in the past is
    # refused (see below), so age a fresh one in the database.
    expired = admin.post("/api/admin/swarms/echo-base/invites",
                         json={"uses": 5,
                               "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    assert expired.status_code == 200, expired.text
    db_exec(update(Invite).where(Invite.id == uuid.UUID(expired.json()["id"]))
            .values(expires_at=datetime(1970, 1, 1, tzinfo=timezone.utc)))
    _, r = _enroll(http, "z3-late", swarm="echo-base", invite=expired.json()["code"])
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "bad_invite"
    assert _member_row(db_exec, "z3-late") is None


def test_invite_with_past_expiry_is_refused(mesh, admin, home):
    r = admin.post("/api/admin/swarms/echo-base/invites",
                   json={"uses": 1, "expires_at": "1970-01-01T00:00:00+00:00"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_expiry"


def test_last_invite_use_is_spent_atomically(app, mesh, admin, db_exec, home):
    """Two enrollments racing for the last use: only one wins.

    Session A reads the invite (one use left), then session B spends that use
    and commits. A's copy is now stale; a read-check-decrement in Python would
    let A through too. The conditional UPDATE (uses_left > 0) refuses it.
    """
    from server.services import enrollment

    code = admin.post("/api/admin/swarms/echo-base/invites", json={"uses": 1}).json()["code"]

    async def race(session_a):
        swarm = await session_a.get(Swarm, home.id)
        now = datetime.now(timezone.utc)
        stale = await session_a.scalar(select(Invite).where(Invite.swarm_id == home.id))
        assert stale.uses_left == 1
        async with app.state.sessionmaker() as session_b:
            await enrollment._use_invite(session_b, await session_b.get(Swarm, home.id), code, now)
            await session_b.commit()
        with pytest.raises(enrollment.ServiceError) as err:
            await enrollment._use_invite(session_a, swarm, code, now)
        return err.value

    err = db_exec(race)
    assert (err.status, err.code) == (403, "bad_invite")
    assert _invite_row(db_exec, home.id).uses_left == 0


# ---------------------------------------------------------------- open

def test_open_mode_happy_path(mesh, admin, db_exec, home, http):
    admin.post("/api/admin/swarms/echo-base/mode", json={"mode": "open"})

    client, r = _enroll(http, "z3-open", swarm="echo-base")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "active"

    client.login()
    assert client.permissions == ["contribute"]

    m = _member_row(db_exec, "z3-open")
    assert m is not None and m.note == "open-enrolled"
    row = _membership(db_exec, m.id, home.id)
    assert row is not None and row.role == "member"

    # Active at once: it can append its own records, with no relay permission.
    client.append("event", summary="signed right away")
    assert client.flush()[0]["ok"] is True
    assert "relay" not in client.permissions

    # ...and it can't submit another member's records as if it were a hub.
    z3 = mesh.member("z3-r0")
    home.add(z3)
    res = client.relay(sign(z3, 1))
    assert res["results"][0]["status"] == "rejected"
    assert res["results"][0]["code"] == "wrong_agent"


def test_open_rate_limit_slow_down(app, http, admin, home):
    # The per-IP enroll rate limiter (5/hour) would block this test; it is not
    # the limit under test, so switch it off for this test.
    app.state.limiters["enroll"].allow = lambda key, cost=1.0, now=None: True

    admin.post("/api/admin/swarms/echo-base/mode", json={"mode": "open"})

    for i in range(20):
        _, r = _enroll(http, f"z3-open-{i:02d}", swarm="echo-base")
        assert r.status_code == 202, r.text

    _, r = _enroll(http, "z3-open-21", swarm="echo-base")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "slow_down"


# ---------------------------------------------------------------- mode validation

def test_mode_and_open_permissions_validation(mesh, admin, db_exec, home, http):
    good = admin.post("/api/admin/swarms/echo-base/mode",
                      json={"mode": "open", "open_permissions": ["contribute", "recall:scoped"]})
    assert good.status_code == 200, good.text
    assert good.json() == {"swarm": "echo-base", "enrollment_mode": "open",
                           "open_permissions": ["contribute", "recall:scoped"]}

    # relay needs a signed hub appointment; policy is an admin power.
    for body in ({"mode": "open", "open_permissions": ["contribute", "relay"]},
                 {"mode": "open", "open_permissions": ["policy"]},
                 {"mode": "open", "open_permissions": ["recall", "relay"]}):
        r = admin.post("/api/admin/swarms/echo-base/mode", json=body)
        assert r.status_code == 422, (body, r.text)
        assert r.json()["error"]["code"] == "bad_permissions"

    r = admin.post("/api/admin/swarms/echo-base/mode", json={"mode": "maybe"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_mode"

    # Changing mode without open_permissions keeps the current value.
    r = admin.post("/api/admin/swarms/echo-base/mode", json={"mode": "invite"})
    assert r.json()["open_permissions"] == ["contribute", "recall:scoped"]
    r = admin.post("/api/admin/swarms/echo-base/mode", json={"mode": "open"})
    assert r.json()["open_permissions"] == ["contribute", "recall:scoped"]

    # Back to approval: enrollments are pending again.
    admin.post("/api/admin/swarms/echo-base/mode", json={"mode": "approval"})
    _, r = _enroll(http, "z3-again", swarm="echo-base")
    assert r.status_code == 202
    assert r.json()["status"] == "pending"
