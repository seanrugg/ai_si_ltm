"""Deleting a single record, leaving a tombstone (issue #173)."""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from ltms_contract import (
    GENESIS_HASH,
    canonicalize,
    record_signing_message,
    verify_signature,
)
from server.models import AuditLog, Record, RecordPayload, RecordTombstone
from server.services import integrity


def _three(mesh, name="z3-r0"):
    m = mesh.member(name)
    m.sent = [
        m.append("event", summary="one", payload={"n": 1}),
        m.append("event", summary="two: secret", payload={"n": 2}, tags=["private"]),
        m.append("event", summary="three", payload={"n": 3}),
    ]
    m.flush()
    return m


def _delete(admin, agent, seq, reason=None):
    return admin.request("DELETE", f"/api/admin/records/{agent}/{seq}",
                         json={"reason": reason} if reason is not None else None)


def _verify(db_exec, name):
    async def go(session):
        from server.models import Member
        m = await session.scalar(select(Member).where(Member.name == name))
        return await integrity.verify_member(session, m)
    return db_exec(go)


def test_delete_middle_record_keeps_the_chain_valid(admin, mesh, db_exec):
    _three(mesh)
    r = _delete(admin, "z3-r0", 2, reason="test data")
    assert r.status_code == 200, r.text
    tomb = r.json()
    assert tomb["deleted"] is True and tomb["seq"] == 2 and tomb["reason"] == "test data"
    assert tomb["deleted_by"].startswith("admin:")

    # Content is gone; only the tombstone's fields remain.
    assert db_exec(select(Record.seq).order_by(Record.seq)).scalars().all() == [1, 3]
    assert db_exec(select(RecordPayload.record_id)).scalars().all().__len__() == 2
    rows = db_exec(select(RecordTombstone)).scalars().all()
    assert len(rows) == 1 and not hasattr(rows[0], "summary")

    report = _verify(db_exec, "z3-r0")
    assert report["status"] == "ok", report
    assert report["records"] == 2 and report["deleted"] == 1

    # Record 3 still links: its prev_hash is the tombstone's hash.
    rec3 = db_exec(select(Record).where(Record.seq == 3)).scalar_one()
    assert rec3.prev_hash == tomb["hash"]


def test_record_detail_resolves_a_deleted_record(admin, mesh):
    _three(mesh)
    _delete(admin, "z3-r0", 2)
    body = admin.get("/api/admin/records/z3-r0/2").json()
    assert body["deleted"] is True and body["seq"] == 2
    assert "summary" not in body and "payload" not in body
    assert admin.get("/api/admin/records/z3-r0/9").status_code == 404


def test_deleting_the_newest_record_then_appending(admin, mesh, db_exec):
    m = _three(mesh)
    assert _delete(admin, "z3-r0", 3).status_code == 200
    m.append("event", summary="four", payload={"n": 4})
    m.flush()
    assert _verify(db_exec, "z3-r0")["status"] == "ok"
    assert db_exec(select(Record.seq).order_by(Record.seq)).scalars().all() == [1, 2, 4]


def test_a_resent_deleted_record_is_a_duplicate(admin, mesh):
    m = _three(mesh)
    _delete(admin, "z3-r0", 2)
    res = m._call("POST", "/api/records", json={"records": [m.sent[1]]})
    assert res["results"][0]["status"] == "duplicate", res
    assert admin.get("/api/admin/records/z3-r0/2").json()["deleted"] is True


def test_the_database_refuses_deletes_without_a_tombstone(mesh, db_exec):
    _three(mesh)
    with pytest.raises((DBAPIError, IntegrityError)):
        db_exec(delete(Record).where(Record.seq == 2))
    with pytest.raises((DBAPIError, IntegrityError)):
        db_exec(update(Record).values(summary="edited"))


def test_tombstones_are_append_only(admin, mesh, db_exec):
    _three(mesh)
    _delete(admin, "z3-r0", 2)
    with pytest.raises((DBAPIError, IntegrityError)):
        db_exec(update(RecordTombstone).values(reason="changed"))
    with pytest.raises((DBAPIError, IntegrityError)):
        db_exec(delete(RecordTombstone))


def test_errors_and_auth(admin, mesh, http):
    _three(mesh)
    assert _delete(admin, "z3-r0", 9).status_code == 404
    assert _delete(admin, "z3-r0", 2).status_code == 200
    r = _delete(admin, "z3-r0", 2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_deleted"
    r = _delete(admin, "z3-r0", 1, reason="x" * 501)
    assert r.status_code == 422
    assert http.request("DELETE", "/api/admin/records/z3-r0/1").status_code == 401
    assert http.get("/api/admin/records/z3-r0/1/delete-preview").status_code == 401


def test_audit_row(admin, mesh, db_exec):
    _three(mesh)
    tomb = _delete(admin, "z3-r0", 1, reason="cleanup").json()
    row = db_exec(select(AuditLog).where(AuditLog.action == "record_delete")).scalar_one()
    assert row.detail == {"agent": "z3-r0", "seq": 1, "hash": tomb["hash"], "reason": "cleanup"}


def test_preview_lists_referrers(admin, mesh):
    z3 = _three(mesh)
    k2 = mesh.member("k2-s0")
    k2.append("lesson", summary="z3 two matters", refs={"because": "z3-r0:2"})
    k2.append("thought", summary="not a ref to 2", refs={"because": ["z3-r0:20", "z3-r0:1"]})
    k2.flush()
    del z3
    body = admin.get("/api/admin/records/z3-r0/2/delete-preview").json()
    assert body["type"] == "event" and body["summary"] == "two: secret"
    assert [(r["agent"], r["seq"]) for r in body["referenced_by"]] == [("k2-s0", 1)]
    assert body["policy_effect"] is None
    # After deletion the referrer keeps its ref, which resolves to the tombstone.
    _delete(admin, "z3-r0", 2)
    assert admin.get("/api/admin/records/k2-s0/1").json()["refs"] == {"because": "z3-r0:2"}
    assert admin.get("/api/admin/records/z3-r0/2").json()["deleted"] is True


def test_policy_effects(admin, mesh):
    p = mesh.member("p0-l1", permissions=["contribute", "recall", "policy"])
    p.append("policy", summary="Rule v1")                                  # seq 1, in force
    p.append("policy", summary="Rule v2", refs={"supersedes": "p0-l1:1"})  # seq 2 retires 1
    p.flush()
    effect = admin.get("/api/admin/records/p0-l1/2/delete-preview").json()["policy_effect"]
    assert "p0-l1:2 is in force" in effect and "p0-l1:1 goes back into force" in effect
    assert admin.get("/api/admin/records/p0-l1/1/delete-preview").json()["policy_effect"] is None
    _delete(admin, "p0-l1", 2)
    statuses = {f"{x['agent']}:{x['seq']}": x["status"]
                for x in admin.get("/api/admin/policies").json()["policies"]}
    assert statuses == {"p0-l1:1": "in_force"}


def test_export_includes_tombstones_and_still_verifies(admin, mesh):
    _three(mesh)
    _delete(admin, "z3-r0", 2, reason="test")
    lines = [json.loads(x) for x in admin.get("/api/admin/export", params={"format": "jsonl"}).text.splitlines()[1:]]
    chain = sorted(lines, key=lambda x: (x.get("agent") or json.loads(x["header"])["agent"],
                                         x.get("seq") or json.loads(x["header"])["seq"]))
    prev = GENESIS_HASH
    for line in chain:
        if line.get("deleted"):
            assert line["prev_hash"] == prev
            prev = line["hash"]
            continue
        header = json.loads(line["header"])
        assert header["prev_hash"] == prev
        assert hashlib.sha256(line["header"].encode()).hexdigest() == line["hash"]
        assert verify_signature(bytes.fromhex(line["public_key"]), record_signing_message(line["hash"]), line["sig"])
        assert hashlib.sha256(canonicalize(line["payload"])).hexdigest() == header["payload_hash"]
        prev = line["hash"]
    assert sum(1 for x in lines if x.get("deleted")) == 1

    rows = admin.get("/api/admin/export", params={"format": "csv"}).text.splitlines()
    assert rows[0].endswith(",deleted")
    assert rows[-1].endswith(",true") and "z3-r0,2," in rows[-1]
    # Filtered by type, a tombstone (which has no type) is left out.
    typed = admin.get("/api/admin/export", params={"format": "jsonl", "type": "event"}).text.splitlines()[1:]
    assert not any(json.loads(x).get("deleted") for x in typed)


def test_truncate_is_still_refused(mesh, db_exec, db_url):
    if not db_url.startswith("postgresql"):
        pytest.skip("TRUNCATE is PostgreSQL only")
    _three(mesh)
    for table in ("records", "record_tombstones"):
        with pytest.raises((DBAPIError, IntegrityError)):
            db_exec(text(f"TRUNCATE {table} CASCADE"))
