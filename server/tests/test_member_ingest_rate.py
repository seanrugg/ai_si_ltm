"""Admin control of a member's ingest rate limit (issue #438 part 2)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from ltms_contract import GENESIS_HASH, build_record
from server.models import AuditLog


def _listed(admin, name):
    r = admin.get("/api/admin/members")
    assert r.status_code == 200, r.text
    return next(m for m in r.json()["members"] if m["name"] == name)


def test_admin_sets_and_clears_ingest_rate(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    url = f"/api/admin/members/{z3.member_id}/ingest-rate"
    assert _listed(admin, "z3-r0")["ingest_rate"] is None

    r = admin.patch(url, json={"ingest_rate": 2.5})
    assert r.status_code == 200, r.text
    assert r.json()["ingest_rate"] == 2.5
    assert _listed(admin, "z3-r0")["ingest_rate"] == 2.5

    r = admin.patch(url, json={"ingest_rate": None})
    assert r.status_code == 200, r.text
    assert r.json()["ingest_rate"] is None

    actions = db_exec(select(AuditLog.detail).where(AuditLog.action == "member_ingest_rate")).scalars().all()
    assert actions == [{"old": None, "new": 2.5}, {"old": 2.5, "new": None}]


@pytest.mark.parametrize("body", [{}, {"ingest_rate": 0}, {"ingest_rate": -1}, {"ingest_rate": "fast"},
                                  {"ingest_rate": True}, {"ingest_rate": 1e9}])
def test_bad_ingest_rate_is_refused(mesh, admin, body):
    z3 = mesh.member("z3-r0")
    r = admin.patch(f"/api/admin/members/{z3.member_id}/ingest-rate", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "bad_ingest_rate"


def test_ingest_rate_needs_an_admin(mesh, http):
    z3 = mesh.member("z3-r0")
    r = http.patch(f"/api/admin/members/{z3.member_id}/ingest-rate", json={"ingest_rate": 1})
    assert r.status_code == 401


def test_member_list_shows_when_ingest_was_refused(mesh, admin, monkeypatch):
    class Frozen:
        @staticmethod
        def monotonic() -> float:
            return 1000.0

    monkeypatch.setattr("server.security.ratelimit.time", Frozen)  # no refill between the two posts
    z3 = mesh.member("z3-r0")
    assert admin.patch(f"/api/admin/members/{z3.member_id}/ingest-rate",
                       json={"ingest_rate": 1}).status_code == 200
    assert _listed(admin, "z3-r0")["ingest_refused_at"] is None

    z3._ensure_token()
    first = build_record(z3.key, agent="z3-r0", seq=1, prev_hash=GENESIS_HASH, type="event")
    second = build_record(z3.key, agent="z3-r0", seq=2, prev_hash=first["hash"], type="event")
    headers = {"Authorization": f"Bearer {z3.token}"}
    assert z3.http.post("/api/records", json={"records": [first]}, headers=headers).status_code == 200
    assert z3.http.post("/api/records", json={"records": [second]}, headers=headers).status_code == 429

    assert _listed(admin, "z3-r0")["ingest_refused_at"] is not None

    # Raising the limit clears the badge: that refusal was under the old limit.
    assert admin.patch(f"/api/admin/members/{z3.member_id}/ingest-rate",
                       json={"ingest_rate": 50}).status_code == 200
    assert _listed(admin, "z3-r0")["ingest_refused_at"] is None


def test_member_list_includes_the_server_default(mesh, admin, app):
    """#451: the dashboard shows "server default (N records/s)" without knowing LTMS_INGEST_RATE."""
    mesh.member("z3-r0")
    body = admin.get("/api/admin/members").json()
    assert body["ingest_rate_default"] == app.state.config.ingest_rate == 1000  # the test config's value
