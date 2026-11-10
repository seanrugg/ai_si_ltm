"""Resuming a paused LRS endpoint: skip or send the backlog (issue #151)."""
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


def _paused_with_backlog(admin, mesh, db_exec, http, app, n=3):
    ep = _endpoint(admin, "lrs-resume", scope={"types": ["event"]})
    z3 = mesh.member("z3-r0")
    z3.append("event", summary="before pause")
    z3.flush()
    assert _forward(app, http, lambda req: httpx.Response(200)) == {"forwarded": 1}
    assert admin.patch(f"/api/admin/lrs/{ep['id']}", json={"enabled": False}).status_code == 200
    for i in range(n):
        z3.append("event", summary=f"during pause {i}")
    z3.flush()
    return ep, z3


def _sent(app, http):
    sent = []
    _forward(app, http, lambda req: sent.extend(json.loads(req.content)) or httpx.Response(200))
    return [s["object"]["definition"]["name"]["en"] for s in sent]


def test_skip_moves_the_cursor_to_the_newest_record(admin, mesh, http, app, db_exec):
    app.state.config.lrs_max_batches_per_run = 20
    ep, z3 = _paused_with_backlog(admin, mesh, db_exec, http, app)
    r = admin.post(f"/api/admin/lrs/{ep['id']}/resume", json={"backlog": "skip"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    assert _endpoint_row(db_exec, "lrs-resume").cursor == _record_ids(db_exec)[-1]
    assert _sent(app, http) == []
    z3.append("event", summary="after resume")
    z3.flush()
    assert _sent(app, http) == ["after resume"]


def test_send_keeps_the_cursor_and_sends_the_backlog(admin, mesh, http, app, db_exec):
    app.state.config.lrs_max_batches_per_run = 20
    ep, _ = _paused_with_backlog(admin, mesh, db_exec, http, app)
    before = _endpoint_row(db_exec, "lrs-resume").cursor
    assert admin.post(f"/api/admin/lrs/{ep['id']}/resume", json={"backlog": "send"}).status_code == 200
    assert _endpoint_row(db_exec, "lrs-resume").cursor == before
    assert _sent(app, http) == ["during pause 0", "during pause 1", "during pause 2"]


def test_audit_row_has_pending_and_both_cursors(admin, mesh, http, app, db_exec):
    ep, _ = _paused_with_backlog(admin, mesh, db_exec, http, app)
    old = _endpoint_row(db_exec, "lrs-resume").cursor
    admin.post(f"/api/admin/lrs/{ep['id']}/resume", json={"backlog": "skip"})

    async def go(session):
        return (await session.scalars(select(AuditLog).where(AuditLog.action == "lrs_resume"))).one()
    row = db_exec(go)
    assert row.detail["backlog_mode"] == "skip"
    assert row.detail["pending"] == 3
    assert row.detail["old_cursor"] == old
    assert row.detail["new_cursor"] == _record_ids(db_exec)[-1]
    assert row.actor.startswith("admin:")


def test_errors(admin, mesh, http, app, db_exec):
    ep, _ = _paused_with_backlog(admin, mesh, db_exec, http, app)
    r = admin.post(f"/api/admin/lrs/{ep['id']}/resume", json={"backlog": "later"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_backlog_mode"
    assert admin.post(f"/api/admin/lrs/{ep['id']}/resume", json={"backlog": "send"}).status_code == 200
    r = admin.post(f"/api/admin/lrs/{ep['id']}/resume", json={"backlog": "send"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_enabled"


def test_requires_admin(http):
    assert http.post("/api/admin/lrs/00000000-0000-0000-0000-000000000000/resume",
                     json={"backlog": "send"}).status_code == 401


def test_patch_cannot_move_the_cursor(admin, mesh, http, app, db_exec):
    ep, _ = _paused_with_backlog(admin, mesh, db_exec, http, app)
    before = _endpoint_row(db_exec, "lrs-resume").cursor
    r = admin.patch(f"/api/admin/lrs/{ep['id']}", json={"cursor": 999999, "name": "lrs-resume"})
    assert r.status_code == 200
    assert _endpoint_row(db_exec, "lrs-resume").cursor == before
