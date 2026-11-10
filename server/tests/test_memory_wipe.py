"""Memory wipe: a clean slate for a member or swarm (issue #157)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ltms_contract.keys import generate_key, public_key_hex
from server.models import AuditLog, LrsEndpoint, Member
from server.services import integrity, lrs as lrs_service, retention, swarms


def _wipe(admin, member, confirm=None):
    return admin.post(f"/api/admin/members/{member.member_id}/wipe",
                      json={"confirm": member.name if confirm is None else confirm})


def _summaries(records):
    return {r["summary"] for r in records}


def _before(mesh):
    """z3 and vector each write one record, and vector sets an in-force policy."""
    z3 = mesh.member("z3-r0")
    vec = mesh.member("vector", permissions=["contribute", "recall", "policy"])
    z3.append("event", summary="z3 old")
    z3.flush()
    vec.append("communication", summary="vector old", peers=["z3-r0"])
    vec.append("policy", summary="Quiet hours", payload={"rule": "no driving after 9pm"})
    vec.flush()
    return z3, vec


def test_wipe_hides_earlier_records_from_any_author(mesh, admin):
    z3, vec = _before(mesh)
    r = _wipe(admin, z3)
    assert r.status_code == 200, r.text
    assert r.json()["memory_generation"] == 1
    assert r.json()["recall_floor_id"] > 0

    assert _summaries(z3.records(limit=200)) == {"Quiet hours"}
    pack = z3.context()
    assert [p["summary"] for p in pack["policies"]] == ["Quiet hours"]
    assert pack["comms"] == [] and pack["outcomes"] == [] and pack["lessons"] == []


def test_records_after_the_wipe_are_recalled(mesh, admin):
    z3, vec = _before(mesh)
    assert _wipe(admin, z3).status_code == 200
    z3.append("event", summary="z3 new")
    z3.flush()
    vec.append("communication", summary="vector new", peers=["z3-r0"])
    vec.flush()
    assert _summaries(z3.records(limit=200)) == {"Quiet hours", "z3 new", "vector new"}
    assert [c["summary"] for c in z3.context(target="vector")["comms"]] == ["vector new"]


def test_chain_continues_and_verifies(mesh, admin, db_exec):
    z3, _ = _before(mesh)
    head_before = z3.sync_head()
    assert _wipe(admin, z3).status_code == 200
    assert z3.sync_head() == head_before
    z3.append("event", summary="after")
    z3.flush()
    assert z3.sync_head()["last_ack_seq"] == head_before["last_ack_seq"] + 1

    async def check(session):
        m = (await session.scalars(select(Member).where(Member.name == "z3-r0"))).one()
        return await integrity.verify_member(session, m)

    assert db_exec(check)["status"] == "ok"


def test_others_and_admin_still_see_the_history(mesh, admin):
    z3, vec = _before(mesh)
    assert _wipe(admin, z3).status_code == 200
    assert "z3 old" in _summaries(vec.records(limit=200))
    timeline = admin.get("/api/admin/timeline", params={"limit": 200}).json()["records"]
    assert {"z3 old", "vector old", "Quiet hours"} <= _summaries(timeline)


def test_lrs_cursor_and_backlog_unaffected(mesh, admin, app, db_exec):
    z3, _ = _before(mesh)

    async def make(session):
        ep = await lrs_service.create_endpoint(
            session, app.state.config, name="lrs-one", url="https://lrs.example.org",
            username="u", secret=None, scope={"types": ["event", "communication", "policy"]}, actor="test")
        return ep.cursor

    cursor = db_exec(make)

    async def state(session):
        ep = (await session.scalars(select(LrsEndpoint).where(LrsEndpoint.name == "lrs-one"))).one()
        return ep.cursor, await lrs_service.backlog(session, ep)

    before = db_exec(state)
    assert before[0] == cursor
    assert _wipe(admin, z3).status_code == 200
    assert db_exec(state) == before


def test_wrong_confirm_and_bad_state(mesh, admin, db_exec):
    z3, _ = _before(mesh)
    r = _wipe(admin, z3, confirm="nope")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_confirm"
    assert "z3 old" in _summaries(z3.records(limit=200))

    assert admin.post(f"/api/admin/members/{z3.member_id}/revoke", json={}).status_code == 200
    r = _wipe(admin, z3)
    assert r.status_code == 409 and r.json()["error"]["code"] == "bad_state"


def test_floor_only_moves_forward_and_generation_counts(mesh, admin):
    z3, _ = _before(mesh)
    first = _wipe(admin, z3).json()
    second = _wipe(admin, z3).json()
    assert second["memory_generation"] == 2
    assert second["recall_floor_id"] >= first["recall_floor_id"]


def test_swarm_wipe_covers_active_members_only(mesh, admin, db_exec):
    z3, vec = _before(mesh)
    pi = mesh.member("pi-zero")
    db_exec(lambda s: swarms.create_swarm(s, name="test-swarm", public_key_hex=public_key_hex(generate_key())))
    for m in (z3, vec, pi):
        r = admin.post("/api/admin/swarms/test-swarm/members", json={"member": m.name, "role": "member"})
        assert r.status_code == 200, r.text
    assert admin.post(f"/api/admin/members/{pi.member_id}/revoke", json={}).status_code == 200

    assert admin.post("/api/admin/swarms/test-swarm/wipe", json={"confirm": "wrong"}).status_code == 422
    r = admin.post("/api/admin/swarms/test-swarm/wipe", json={"confirm": "test-swarm"})
    assert r.status_code == 200, r.text
    assert sorted(w["name"] for w in r.json()["wiped"]) == ["vector", "z3-r0"]
    assert _summaries(z3.records(limit=200)) == {"Quiet hours"}
    assert _summaries(vec.records(limit=200)) == {"Quiet hours"}


def test_wipe_is_audited_and_never_pruned(mesh, admin, db_exec):
    z3, _ = _before(mesh)
    assert _wipe(admin, z3).status_code == 200
    old = datetime.now(timezone.utc) - timedelta(days=400)

    async def age(session):
        session.add(AuditLog(actor="test", action="admin_login", detail={}, ts=old))
        for row in await session.scalars(select(AuditLog).where(AuditLog.action == "memory_wipe")):
            row.ts = old

    db_exec(age)
    pruned = db_exec(lambda s: retention.prune_audit_log(s, 365))
    assert pruned >= 1

    async def wipes(session):
        return list(await session.scalars(select(AuditLog).where(AuditLog.action == "memory_wipe")))

    rows = db_exec(wipes)
    assert len(rows) == 1
    assert rows[0].detail["name"] == "z3-r0" and rows[0].detail["generation"] == 1


def test_admin_only(mesh, http):
    z3, _ = _before(mesh)
    assert http.post(f"/api/admin/members/{z3.member_id}/wipe", json={"confirm": "z3-r0"}).status_code == 401
    assert http.post("/api/admin/swarms/any/wipe", json={"confirm": "any"}).status_code == 401
