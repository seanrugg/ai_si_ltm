"""Incremental integrity checks from per-member checkpoints, and the background full check (#439)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update

from server.config import load_config
from server.models import IntegrityCheckpoint, IntegrityRun, Member, Record
from server.services import integrity, retention


def run_full_check(admin, timeout: float = 30) -> dict:
    """Start a full check from the API and wait for its background task to finish.

    It waits on the task itself instead of polling GET /admin/integrity/full:
    in tests the in-memory SQLite database is one shared connection, and status
    requests running alongside the check interleave their transactions with it,
    which made the check miss an edit or stall now and then on busy CI runners.
    """
    r = admin.post("/api/admin/integrity/full")
    assert r.status_code == 202, r.text
    assert r.json()["run"]["mode"] == "full"
    tasks = list(getattr(admin.http.app.state, "integrity_tasks", ()))

    async def wait():
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)
    admin.http.portal.call(wait)
    run = admin.get("/api/admin/integrity/full").json()["run"]
    assert run["status"] != "running", "full check did not finish"
    return run


def _check(admin, name=None) -> dict:
    r = admin.get("/api/admin/integrity", params={"member": name} if name else None)
    assert r.status_code == 200, r.text
    reps = {m["member"]: m for m in r.json()["members"]}
    return reps[name] if name else reps


def _append(m, n, start=0, **kw):
    for i in range(start, start + n):
        m.append("event", summary=f"e{i}", payload={"i": i}, **kw)
    m.flush()


def _unguard_records(app, db_exec):
    """Simulate a database superuser editing history behind the append-only trigger."""
    if app.state.engine.dialect.name == "postgresql":
        db_exec(text("ALTER TABLE records DISABLE TRIGGER trg_records_no_mutation"))
    else:
        db_exec(text("DROP TRIGGER trg_records_no_update"))


def _delete(admin, agent, seq):
    r = admin.request("DELETE", f"/api/admin/records/{agent}/{seq}", json={"reason": "test"})
    assert r.status_code == 200, r.text


def test_checkpoint_created_then_unchanged_fleet_checks_nothing(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    _append(z3, 4)
    first = _check(admin, "z3-r0")
    assert first["status"] == "ok" and first["mode"] == "incremental"
    assert first["records"] == 4 and first["checked"] == 4 and first["verified_through"] == 4
    cp = db_exec(select(IntegrityCheckpoint)).scalar_one()
    assert (cp.last_verified_seq, cp.records, cp.payloads_present, cp.status) == (4, 4, 4, "ok")

    again = _check(admin, "z3-r0")
    assert again["checked"] == 0 and again["status"] == "ok"
    assert again["records"] == 4 and again["payloads_present"] == 4 and again["verified_through"] == 4
    assert "checkpoint_reset" not in again

    _append(z3, 2, start=4)
    more = _check(admin, "z3-r0")
    assert more["checked"] == 2 and more["records"] == 6 and more["verified_through"] == 6


def test_empty_member_gets_a_genesis_checkpoint(mesh, admin):
    mesh.member("quiet")
    rep = _check(admin, "quiet")
    assert rep["status"] == "empty" and rep["verified_through"] == 0
    assert _check(admin, "quiet")["status"] == "empty"


def test_tampering_after_the_checkpoint_is_caught_incrementally(app, mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    _append(z3, 3)
    assert _check(admin, "z3-r0")["verified_through"] == 3
    _append(z3, 3, start=3)
    _unguard_records(app, db_exec)
    db_exec(text("UPDATE records SET summary = 'edited' WHERE seq = 5"))
    rep = _check(admin, "z3-r0")
    assert rep["status"] == "broken" and rep["error"]["seq"] == 5 and rep["error"]["code"] == "column_mismatch"
    # The checkpoint stays where it was verified, so the break keeps showing.
    assert db_exec(select(IntegrityCheckpoint.last_verified_seq)).scalar() == 3
    assert _check(admin, "z3-r0")["status"] == "broken"


def test_tampering_before_the_checkpoint_needs_a_full_check(app, mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    _append(z3, 4)
    assert _check(admin, "z3-r0")["status"] == "ok"
    _unguard_records(app, db_exec)
    db_exec(text("UPDATE records SET outcome = 'failed' WHERE seq = 2"))
    assert _check(admin, "z3-r0")["status"] == "ok"  # behind the checkpoint: incremental can't see it

    run = run_full_check(admin)
    assert run["status"] == "done" and run["broken"] == 1 and run["members_done"] == run["members_total"] == 1
    assert run["records_done"] == 1  # stopped at the break in seq 2 (one record verified before it)
    # The full result is stored, and the checkpoint is back at genesis, so the
    # next incremental check finds the break again instead of trusting it.
    stored = {r["member"]: r for r in db_exec(integrity.latest_results)}["z3-r0"]
    assert stored["status"] == "broken" and stored["mode"] == "full" and stored["error"]["seq"] == 2
    rep = _check(admin, "z3-r0")
    assert rep["status"] == "broken" and rep["error"]["seq"] == 2 and rep["mode"] == "incremental"


def test_full_check_rewrites_checkpoints_and_reports_progress(mesh, admin, db_exec):
    a, b = mesh.member("alpha"), mesh.member("bravo")
    _append(a, 5)
    _append(b, 3)
    run = run_full_check(admin)
    assert run["status"] == "done" and run["broken"] == 0
    assert (run["members_total"], run["members_done"], run["records_done"]) == (2, 2, 8)
    assert run["started_by"] == "admin" and run["finished_at"]
    cps = {c.member_id: c for c in db_exec(select(IntegrityCheckpoint)).scalars()}
    assert sorted((c.last_verified_seq, c.mode) for c in cps.values()) == [(3, "full"), (5, "full")]
    assert _check(admin, "alpha")["checked"] == 0
    full = admin.get("/api/admin/integrity").json()["full"]
    assert full["id"] == run["id"] and full["status"] == "done"
    assert admin.get("/api/admin/recall-log", params={"action": "integrity_full_check"}).json()["entries"]


def test_only_one_full_check_at_a_time_and_stale_runs_expire(db_exec):
    db_exec(lambda s: integrity.start_full_run(s, started_by="a"))

    async def second(session):
        try:
            await integrity.start_full_run(session, started_by="b")
        except Exception as exc:  # noqa: BLE001
            return getattr(exc, "code", None)
        return None
    assert db_exec(second) == "already_running"

    old = datetime.now(timezone.utc) - integrity.RUN_STALE_AFTER - timedelta(minutes=1)
    db_exec(update(IntegrityRun).values(updated_at=old))
    latest = db_exec(integrity.latest_run)
    assert latest.status == "failed" and "interrupted" in latest.error
    assert db_exec(lambda s: integrity.start_full_run(s, started_by="c")).status == "running"
    assert db_exec(select(IntegrityRun).where(IntegrityRun.status == "running")).scalars().all().__len__() == 1


def test_delete_at_or_before_the_checkpoint_resets_it(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    _append(z3, 4)
    _check(admin, "z3-r0")
    _delete(admin, "z3-r0", 2)
    rep = _check(admin, "z3-r0")
    assert rep["checkpoint_reset"] == "a verified record was deleted"
    assert rep["status"] == "ok" and rep["records"] == 3 and rep["deleted"] == 1 and rep["checked"] == 4
    again = _check(admin, "z3-r0")
    assert again["checked"] == 0 and again["deleted"] == 1 and "checkpoint_reset" not in again

    _delete(admin, "z3-r0", 4)  # the checkpoint record itself
    rep = _check(admin, "z3-r0")
    assert rep["checkpoint_reset"] and rep["status"] == "ok" and rep["records"] == 2 and rep["deleted"] == 2


def test_delete_after_the_checkpoint_needs_no_reset(mesh, admin):
    z3 = mesh.member("z3-r0")
    _append(z3, 3)
    _check(admin, "z3-r0")
    _append(z3, 3, start=3)
    _delete(admin, "z3-r0", 5)
    rep = _check(admin, "z3-r0")
    assert "checkpoint_reset" not in rep
    assert rep["status"] == "ok" and rep["records"] == 5 and rep["deleted"] == 1 and rep["verified_through"] == 6


def test_purged_payloads_move_the_counts_without_a_reset(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    z3.append("perception", ts=old, summary="old", payload={"v": 1})
    z3.append("perception", ts=old, summary="old 2", payload={"v": 2})
    z3.flush()
    _check(admin, "z3-r0")
    z3.append("perception", ts=old, summary="old, after the checkpoint", payload={"v": 3})
    z3.flush()
    purged = db_exec(lambda s: retention.purge_payloads(s, load_config().retention_payload_rules))
    assert purged == 3
    cp = db_exec(select(IntegrityCheckpoint)).scalar_one()
    assert (cp.payloads_present, cp.payloads_purged) == (0, 2)
    rep = _check(admin, "z3-r0")
    assert "checkpoint_reset" not in rep and rep["checked"] == 1
    assert rep["status"] == "ok" and rep["payloads_present"] == 0 and rep["payloads_purged"] == 3


def test_a_new_key_voids_the_checkpoint(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    mesh.member("other")
    _append(z3, 2)
    _check(admin, "z3-r0")
    other_key = db_exec(select(Member.public_key).where(Member.name == "other")).scalar()
    db_exec(text("UPDATE members SET public_key = :k WHERE name = 'other'").bindparams(k=b"\x01" * 32))
    db_exec(text("UPDATE members SET public_key = :k WHERE name = 'z3-r0'").bindparams(k=other_key))
    rep = _check(admin, "z3-r0")
    assert rep["checkpoint_reset"] == "the member's key changed"
    assert rep["status"] == "broken" and rep["error"]["code"] == "sig" and rep["error"]["seq"] == 1


def test_budget_makes_long_chains_partial_and_carries_on(mesh, db_exec):
    z3 = mesh.member("z3-r0")
    _append(z3, 5)
    first = db_exec(lambda s: integrity.check_all(s, budget=2))[0]
    assert first["status"] == "partial" and first["verified_through"] == 2 and first["checked"] == 2
    second = db_exec(lambda s: integrity.check_all(s, budget=2))[0]
    assert second["status"] == "partial" and second["verified_through"] == 4
    third = db_exec(lambda s: integrity.check_all(s, budget=2))[0]
    assert third["status"] == "ok" and third["verified_through"] == 5 and third["records"] == 5
    # Exactly the budget left at the head is complete, not partial.
    _append(z3, 2, start=5)
    assert db_exec(lambda s: integrity.check_all(s, budget=2))[0]["status"] == "ok"


def test_latest_results_reads_stored_results_only(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    mesh.member("never-checked")
    _append(z3, 3)
    _check(admin, "z3-r0")
    _append(z3, 2, start=3)  # not checked yet: latest_results must not verify anything
    rows = {r["member"]: r for r in db_exec(integrity.latest_results)}
    assert set(rows["z3-r0"]) >= {"member", "status", "records", "head_seq", "payloads_present",
                                  "payloads_purged", "error", "verified_at", "mode"}
    assert rows["z3-r0"]["records"] == 3 and rows["z3-r0"]["head_seq"] == 3 and rows["z3-r0"]["mode"] == "incremental"
    assert rows["z3-r0"]["verified_at"]
    assert rows["never-checked"]["status"] == "unchecked" and rows["never-checked"]["records"] is None


def test_verify_member_still_reads_only(mesh, db_exec):
    z3 = mesh.member("z3-r0")
    _append(z3, 2)

    async def go(session):
        m = await session.scalar(select(Member).where(Member.name == "z3-r0"))
        return await integrity.verify_member(session, m)
    assert db_exec(go)["status"] == "ok"
    assert db_exec(select(IntegrityCheckpoint)).scalars().all() == []
    assert db_exec(select(Record.seq)).scalars().all() == [1, 2]
