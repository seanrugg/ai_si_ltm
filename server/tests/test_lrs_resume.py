"""The LRS gets only records ingested while its endpoint is on (issue #175)."""
from __future__ import annotations

import json

import httpx
from sqlalchemy import select

from server.models import AuditLog
from server.tests.test_lrs_forward import (  # noqa: F401  (fixtures and helpers)
    _endpoint,
    _endpoint_row,
    _forward,
    _record_ids,
    config,
)


def _sent(app, http, status=200):
    sent = []
    _forward(app, http, lambda req: sent.extend(json.loads(req.content)) or httpx.Response(status))
    return [s["object"]["definition"]["name"]["en"] for s in sent]


def _events(member, *summaries):
    for s in summaries:
        member.append("event", summary=s)
    member.flush()


def test_a_new_endpoint_starts_from_now(admin, mesh, http, app, db_exec):
    app.state.config.lrs_max_batches_per_run = 20
    z3 = mesh.member("z3-r0")
    _events(z3, "history 1", "history 2")
    _endpoint(admin, "lrs-live", scope={"types": ["event"]})
    assert _endpoint_row(db_exec, "lrs-live").cursor == _record_ids(db_exec)[-1]
    assert _sent(app, http) == []
    _events(z3, "after create")
    assert _sent(app, http) == ["after create"]


def test_records_written_while_paused_are_never_sent(admin, mesh, http, app, db_exec):
    app.state.config.lrs_max_batches_per_run = 20
    ep = _endpoint(admin, "lrs-live", scope={"types": ["event"]})
    z3 = mesh.member("z3-r0")
    _events(z3, "before pause")
    assert _sent(app, http) == ["before pause"]
    assert admin.patch(f"/api/admin/lrs/{ep['id']}", json={"enabled": False}).status_code == 200
    _events(z3, "paused 1", "paused 2", "paused 3")
    r = admin.patch(f"/api/admin/lrs/{ep['id']}", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert _sent(app, http) == []
    _events(z3, "after resume")
    assert _sent(app, http) == ["after resume"]


def test_resume_audit_row(admin, mesh, http, app, db_exec):
    ep = _endpoint(admin, "lrs-live", scope={"types": ["event"]})
    admin.patch(f"/api/admin/lrs/{ep['id']}", json={"enabled": False})
    old = _endpoint_row(db_exec, "lrs-live").cursor
    _events(mesh.member("z3-r0"), "paused 1", "paused 2")
    admin.patch(f"/api/admin/lrs/{ep['id']}", json={"enabled": True})

    async def go(session):
        rows = await session.scalars(select(AuditLog).where(AuditLog.action == "lrs_resume").order_by(AuditLog.id))
        return rows.all()[-1]
    row = db_exec(go)
    assert row.detail["skipped"] == 2
    assert row.detail["old_cursor"] == old
    assert row.detail["new_cursor"] == _record_ids(db_exec)[-1]


def test_an_outage_while_on_is_retried(admin, mesh, http, app, db_exec):
    app.state.config.lrs_max_batches_per_run = 20
    _endpoint(admin, "lrs-live", scope={"types": ["event"]})
    _events(mesh.member("z3-r0"), "during outage")
    assert _sent(app, http, status=503) == ["during outage"]  # attempted, not acknowledged
    assert _sent(app, http) == ["during outage"]  # sent once the LRS is back


def test_patch_cannot_move_the_cursor(admin, mesh, http, app, db_exec):
    ep = _endpoint(admin, "lrs-live", scope={"types": ["event"]})
    _events(mesh.member("z3-r0"), "one")
    before = _endpoint_row(db_exec, "lrs-live").cursor
    r = admin.patch(f"/api/admin/lrs/{ep['id']}", json={"cursor": 0, "name": "lrs-live"})
    assert r.status_code == 200
    assert _endpoint_row(db_exec, "lrs-live").cursor == before


def test_the_old_resume_route_is_gone(admin):
    r = admin.post("/api/admin/lrs/00000000-0000-0000-0000-000000000000/resume", json={"backlog": "send"})
    assert r.status_code in (404, 405)
