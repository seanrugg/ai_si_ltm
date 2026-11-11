"""Dataset reads go to the LRS as xAPI statements (issue #239)."""
from __future__ import annotations

import json

import httpx
import pytest

from server.tests.test_datasets_read import _dataset, _get, data_dir  # noqa: F401
from server.tests.test_lrs_forward import (  # noqa: F401  (fixtures and helpers)
    _endpoint,
    _forward,
    config,
)

READ_VERB = "https://w3id.org/xapi-ai/verbs/read-resource"


def _sent(app, http, status=200):
    sent = []
    _forward(app, http, lambda req: sent.extend(json.loads(req.content)) or httpx.Response(status))
    return sent


def _read_keys(statements):
    return [s["object"]["definition"]["name"]["en"] for s in statements if s["verb"]["id"] == READ_VERB]


@pytest.fixture
def setup(admin, mesh, app):
    app.state.config.lrs_max_batches_per_run = 20
    did = _dataset(admin)
    z3 = mesh.member("z3-r0")
    return did, z3


def test_a_read_becomes_a_statement(setup, admin, app, http):
    did, z3 = setup
    _endpoint(admin, "lrs-reads", scope={"types": ["event"]})
    cycle = "5b0f6a3e-2c1d-4e8f-9a7b-1c2d3e4f5a6b"
    assert _get(z3, f"/api/datasets/{did}/items/drive.md", cycle=cycle).status_code == 200
    [stmt] = [s for s in _sent(app, http) if s["verb"]["id"] == READ_VERB]
    assert stmt["actor"]["account"]["name"] == "z3-r0"
    assert stmt["object"]["id"].endswith(f"/datasets/{did}/v/1/items/drive.md")
    ctx = stmt["context"]
    assert ctx["registration"] == cycle
    assert ctx["contextActivities"]["parent"][0]["definition"]["name"]["en"] == "Manuals"
    ext = ctx["extensions"]
    assert ext["https://w3id.org/xapi-ai/extensions/resource-version"] == "1"
    assert len(ext["https://w3id.org/xapi-ai/extensions/resource-hash"]) == 64
    assert ext["https://w3id.org/xapi-ai/extensions/read-source"] == "served"
    assert _read_keys(_sent(app, http)) == []  # cursor advanced: nothing resent


def test_scope_filters_do_not_apply_to_reads(setup, admin, app, http):
    did, z3 = setup
    _endpoint(admin, "lrs-reads", scope={"types": []})  # deny-all for records
    _get(z3, f"/api/datasets/{did}/items/notes.txt")
    assert _read_keys(_sent(app, http)) == ["notes.txt"]


def test_reads_before_the_endpoint_existed_are_not_sent(setup, admin, app, http):
    did, z3 = setup
    _get(z3, f"/api/datasets/{did}/items/drive.md")
    _endpoint(admin, "lrs-reads", scope={"types": ["event"]})
    assert _read_keys(_sent(app, http)) == []
    _get(z3, f"/api/datasets/{did}/items/notes.txt")
    assert _read_keys(_sent(app, http)) == ["notes.txt"]


def test_reads_while_paused_are_never_sent(setup, admin, app, http):
    did, z3 = setup
    ep = _endpoint(admin, "lrs-reads", scope={"types": ["event"]})
    admin.patch(f"/api/admin/lrs/{ep['id']}", json={"enabled": False})
    _get(z3, f"/api/datasets/{did}/items/drive.md")
    assert _read_keys(_sent(app, http)) == []
    admin.patch(f"/api/admin/lrs/{ep['id']}", json={"enabled": True})
    assert _read_keys(_sent(app, http)) == []
    _get(z3, f"/api/datasets/{did}/items/notes.txt")
    assert _read_keys(_sent(app, http)) == ["notes.txt"]


def test_send_reads_false_sends_none(setup, admin, app, http, db_exec):
    did, z3 = setup
    ep = _endpoint(admin, "lrs-reads", scope={"types": ["event"]})
    r = admin.patch(f"/api/admin/lrs/{ep['id']}", json={"send_reads": False})
    assert r.status_code == 200 and r.json()["send_reads"] is False
    _get(z3, f"/api/datasets/{did}/items/drive.md")
    assert _read_keys(_sent(app, http)) == []
    # Switching back on is live-only too.
    admin.patch(f"/api/admin/lrs/{ep['id']}", json={"send_reads": True})
    assert _read_keys(_sent(app, http)) == []
    assert admin.patch(f"/api/admin/lrs/{ep['id']}", json={"send_reads": "yes"}).status_code == 422


def test_create_can_switch_reads_off(admin):
    r = admin.post("/api/admin/lrs", json={"name": "lrs-quiet", "url": "https://lrs.example.org/",
                                           "username": "a", "secret": "s", "send_reads": False})
    assert r.status_code == 200, r.text
    assert r.json()["send_reads"] is False


def test_an_outage_keeps_reads_for_a_retry(setup, admin, app, http):
    did, z3 = setup
    _endpoint(admin, "lrs-reads", scope={"types": ["event"]})
    _get(z3, f"/api/datasets/{did}/items/drive.md")
    assert _read_keys(_sent(app, http, status=503)) == ["drive.md"]
    assert _read_keys(_sent(app, http)) == ["drive.md"]
    assert _read_keys(_sent(app, http)) == []
