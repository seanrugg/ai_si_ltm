"""Integrity re-verification and retention purges."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from server.models import Record
from server.services import retention


def test_integrity_ok_then_purge_keeps_chain(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    z3.append("perception", ts=old, summary="old sensor", payload={"v": 1})
    z3.append("action", ts=old, summary="old action", outcome="success", payload={"d": 0.5})
    z3.append("event", summary="recent event", payload={"v": 2})
    z3.flush()
    rep = admin.get("/api/admin/integrity").json()["members"][0]
    assert rep["status"] == "ok" and rep["records"] == 3 and rep["payloads_purged"] == 0

    purged = db_exec(lambda s: retention.purge_event_payloads(s, days=90))
    assert purged == 1  # only the old perception; actions are kept forever
    rep = admin.get("/api/admin/integrity").json()["members"][0]
    assert rep["status"] == "ok" and rep["payloads_purged"] == 1
    rec = z3.records(type="perception")[0]
    assert rec["payload"] is None and rec["payload_purged"] is True
    assert admin.get("/api/admin/recall-log", params={"action": "retention_purge"}).json()["entries"]


def test_integrity_detects_tampering_behind_the_trigger(app, mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    for i in range(3):
        z3.append("action", summary=f"a{i}", outcome="success")
    z3.flush()
    # Simulate someone with DB superuser rights editing history.
    if app.state.engine.dialect.name == "postgresql":
        db_exec(text("ALTER TABLE records DISABLE TRIGGER trg_records_no_mutation"))
    else:
        db_exec(text("DROP TRIGGER trg_records_no_update"))
    db_exec(text("UPDATE records SET outcome = 'failed' WHERE seq = 2"))
    rep = admin.get("/api/admin/integrity", params={"member": "z3-r0"}).json()["members"][0]
    assert rep["status"] == "broken" and rep["error"]["seq"] == 2 and rep["error"]["code"] == "column_mismatch"

    db_exec(text("UPDATE records SET outcome = 'success' WHERE seq = 2"))
    raw = db_exec(select(Record.header_raw).where(Record.seq == 3)).scalar()
    db_exec(text("UPDATE records SET header_raw = :b WHERE seq = 3").bindparams(b=raw.replace(b"a2", b"a9")))
    rep = admin.get("/api/admin/integrity", params={"member": "z3-r0"}).json()["members"][0]
    assert rep["error"] == {"seq": 3, "code": "hash", "message": "stored header bytes do not match hash"}


def test_expired_auth_cleanup(mesh, db_exec):
    z3 = mesh.member("z3-r0")
    z3.login()
    counts = db_exec(lambda s: retention.purge_expired_auth(s, now=datetime.now(timezone.utc) + timedelta(days=2)))
    assert counts["tokens"] == 1
