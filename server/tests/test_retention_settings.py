"""Admin-editable data retention, including keep forever (issue #155)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from server.models import AuditLog, RecordPayload
from server.services import retention, runtime_settings


def _payload_count(db_exec):
    async def go(session):
        return len(list(await session.scalars(select(RecordPayload.record_id))))
    return db_exec(go)


def _run(db_exec, app, job, **kw):
    async def go(session):
        result = await job(session, app.state.config, **kw)
        await session.commit()
        return result
    return db_exec(go)


def _records(mesh, n=2):
    z3 = mesh.member("z3-r0")
    for i in range(n):
        z3.append("event", summary=f"record {i}", payload={"i": i})
    z3.flush()


def _later(days):
    """Run the purge as if `days` had passed (records are append-only, so their ts can't be aged)."""
    return datetime.now(timezone.utc) + timedelta(days=days)


def test_environment_values_with_no_row(admin, app):
    r = admin.get("/api/admin/retention")
    assert r.status_code == 200, r.text
    body = r.json()
    cfg = app.state.config
    assert body["source"] == "environment"
    assert body["audit_days"] == cfg.retention_audit_days
    assert body["payload_rules"] == [rule.model_dump() for rule in cfg.retention_payload_rules]
    assert body["locked"] is False and body["updated_at"] is None


def test_put_applies_at_the_next_run_without_restart(admin, mesh, app, db_exec):
    _records(mesh)
    app.state.config.retention_payload_rules = []  # environment: keep everything
    assert _run(db_exec, app, retention.run_payload_purge, now=_later(10)) == 0
    assert _payload_count(db_exec) == 2

    r = admin.put("/api/admin/retention", json={"payload_rules": [{"type": "event", "days": 5}], "audit_days": 365})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "dashboard" and r.json()["updated_by"].startswith("admin:")
    assert _run(db_exec, app, retention.run_payload_purge, now=_later(10)) == 2
    assert _payload_count(db_exec) == 0


def test_empty_rules_purge_nothing_even_for_old_records(admin, mesh, app, db_exec):
    _records(mesh)
    assert admin.put("/api/admin/retention", json={"payload_rules": [], "audit_days": None}).status_code == 200
    assert _run(db_exec, app, retention.run_payload_purge, now=_later(4000)) == 0
    assert _payload_count(db_exec) == 2


def test_audit_days_null_prunes_nothing(admin, app, db_exec):
    async def old_row(session):
        session.add(AuditLog(actor="system", action="retention_purge", detail={},
                             ts=datetime.now(timezone.utc) - timedelta(days=5000)))
        await session.commit()
    db_exec(old_row)
    assert admin.put("/api/admin/retention", json={"payload_rules": [], "audit_days": None}).status_code == 200
    assert _run(db_exec, app, retention.run_audit_prune) is None

    async def count(session):
        return len(list(await session.scalars(select(AuditLog.id).where(AuditLog.action == "retention_purge"))))
    assert db_exec(count) == 1

    assert admin.put("/api/admin/retention", json={"payload_rules": [], "audit_days": 30}).status_code == 200
    assert _run(db_exec, app, retention.run_audit_prune) >= 1
    assert db_exec(count) == 0


def test_protected_rules_and_short_audit_days_are_refused(admin):
    for rule in ({"type": "policy", "days": 30}, {"type": "system", "days": 30}, {"tag": "keep", "days": 30},
                 {"days": 30}, {"type": "event", "days": 0}, {"type": "nonsense", "days": 30}):
        r = admin.put("/api/admin/retention", json={"payload_rules": [rule], "audit_days": None})
        assert r.status_code == 422, rule
        assert r.json()["error"]["code"] == "bad_retention", rule
    r = admin.put("/api/admin/retention", json={"payload_rules": [], "audit_days": 29})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_retention"


def test_locked_refuses_and_environment_still_applies(admin, app, db_exec):
    assert admin.put("/api/admin/retention", json={"payload_rules": [], "audit_days": None}).status_code == 200
    app.state.config.retention_locked = True
    try:
        r = admin.put("/api/admin/retention", json={"payload_rules": [], "audit_days": 60})
        assert r.status_code == 409 and r.json()["error"]["code"] == "locked"
        body = admin.get("/api/admin/retention").json()
        assert body["source"] == "environment" and body["locked"] is True
        assert body["audit_days"] == app.state.config.retention_audit_days

        async def resolved(session):
            return await runtime_settings.resolve_retention(session, app.state.config)
        assert db_exec(resolved)["source"] == "environment"
    finally:
        app.state.config.retention_locked = False


def test_audit_row_has_old_and_new(admin, db_exec):
    admin.put("/api/admin/retention", json={"payload_rules": [{"tag": "telemetry", "days": 7}], "audit_days": 90})

    async def go(session):
        return (await session.scalars(select(AuditLog).where(AuditLog.action == "retention_change"))).one()
    row = db_exec(go)
    assert row.detail["old"]["source"] == "environment"
    assert row.detail["new"]["audit_days"] == 90
    assert row.detail["new"]["payload_rules"] == [{"type": None, "tag": "telemetry", "days": 7}]


def test_requires_admin(http):
    assert http.get("/api/admin/retention").status_code == 401
    assert http.put("/api/admin/retention", json={"payload_rules": [], "audit_days": None}).status_code == 401
