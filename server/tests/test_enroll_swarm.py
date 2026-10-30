"""Enrollment names a swarm; the admin confirms on approval (issue #45)."""
from __future__ import annotations

import pytest
import uuid
from sqlalchemy import select

from cli_agent.client import LTMSClient
from ltms_contract import generate_key, public_key_hex
from server.models import EnrollmentSwarmRequest, Member, Swarm
from server.services import swarms
from server.tests.test_swarms import Home


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _mid(member: dict) -> uuid.UUID:
    return uuid.UUID(member["id"])


def _enroll(http, name, swarm=None):
    client = LTMSClient(name=name, signing_key=generate_key(), http=http)
    body = {"name": name, "kind": "robot",
            "public_key": public_key_hex(client.key),
            "permissions": ["contribute"]}
    if swarm is not None:
        body["swarm"] = swarm
    return client, http.post("/api/auth/enroll", json=body)


def test_enroll_naming_swarm_then_approve(mesh, admin, db_exec, home, http):
    _client, r = _enroll(http, "z3-swarm", swarm="echo-base")
    assert r.status_code == 202, r.text
    member_id = r.json()["id"]

    # The admin can see the request before approving.
    req = admin.get(f"/api/admin/members/{member_id}/swarm-request")
    assert req.status_code == 200
    assert req.json() == {"swarm": "echo-base"}

    # Approving joins the swarm as a plain member (never hub).
    m = admin.post(f"/api/admin/members/{member_id}/approve", json={}).json()
    assert m["status"] == "active"
    mid = _mid(m)

    row = db_exec(lambda s: s.execute(
        select(EnrollmentSwarmRequest).where(EnrollmentSwarmRequest.member_id == mid)))
    assert row.one_or_none() is None  # request row gone

    open_membership = db_exec(lambda s: swarms.open_membership(s, mid))
    assert open_membership is not None
    assert open_membership.role == "member"
    assert open_membership.swarm_id == home.id


def test_approve_join_swarm_false(mesh, admin, db_exec, home, http):
    _client, r = _enroll(http, "z3-noswarm", swarm="echo-base")
    assert r.status_code == 202, r.text
    member_id = r.json()["id"]

    m = admin.post(f"/api/admin/members/{member_id}/approve",
                   json={"join_swarm": False}).json()
    assert m["status"] == "active"
    mid = _mid(m)

    row = db_exec(lambda s: s.execute(
        select(EnrollmentSwarmRequest).where(EnrollmentSwarmRequest.member_id == mid)))
    assert row.one_or_none() is None  # request row gone

    open_membership = db_exec(lambda s: swarms.open_membership(s, mid))
    assert open_membership is None  # in no swarm


def test_enroll_unknown_swarm_404_no_member(mesh, http, db_exec):
    client = LTMSClient(name="z3-nowhere", signing_key=generate_key(), http=http)
    body = {"name": "z3-nowhere", "kind": "robot",
            "public_key": public_key_hex(client.key),
            "permissions": ["contribute"], "swarm": "no-such-swarm"}
    r = http.post("/api/auth/enroll", json=body)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "no_swarm"
    # No member was created.
    assert db_exec(lambda s: s.scalar(
        select(Member).where(Member.name == "z3-nowhere"))) is None


def test_reject_removes_request(mesh, admin, db_exec, home, http):
    _client, r = _enroll(http, "z3-reject", swarm="echo-base")
    assert r.status_code == 202, r.text
    member_id = r.json()["id"]

    admin.post(f"/api/admin/members/{member_id}/reject", json={"reason": "nope"})

    row = db_exec(lambda s: s.execute(
        select(EnrollmentSwarmRequest).where(EnrollmentSwarmRequest.member_id == uuid.UUID(member_id))))
    assert row.one_or_none() is None  # request row gone


def test_enroll_without_swarm(mesh, admin, http):
    _client, r = _enroll(http, "z3-plain")  # no swarm named
    assert r.status_code == 202, r.text
    member_id = r.json()["id"]
    assert admin.get(f"/api/admin/members/{member_id}/swarm-request").json() == {"swarm": None}
    m = admin.post(f"/api/admin/members/{member_id}/approve", json={}).json()
    assert m["status"] == "active"
