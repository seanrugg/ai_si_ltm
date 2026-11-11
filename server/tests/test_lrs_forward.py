"""LRS forwarder: per-endpoint background forwarding (issue #61).

No network: the LRS is stubbed with httpx.MockTransport.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, event, select

from ltms_contract.canonical import canonicalize
from server.config import load_config
from server.models import AuditLog, LrsEndpoint, Record, RecordPayload
from server.services import lrs as lrs_service
from server.tests.conftest import ADMIN_PASS, ADMIN_USER
from server.xapi.translate import EXT, NAMESPACE


@pytest.fixture
def config(db_url):
    return load_config(
        database_url=db_url,
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        cookie_secure=False,
        enable_scheduler=False,
        ingest_rate=1000,
        recall_rate=1000,
        secret_key=Fernet.generate_key().decode(),
        lrs_batch_size=2,
        lrs_max_batches_per_run=1,  # one batch per run unless a test raises it
        public_base_url="https://ltms.example.org",
    )


def _endpoint(admin, name, url="https://lrs.example.org/", secret="s3cret", scope=None,
              enabled=True, xapi_version="2.0.0"):
    body = {"name": name, "url": url, "username": "alice", "secret": secret,
            "xapi_version": xapi_version, "scope": scope}
    r = admin.post("/api/admin/lrs", json=body)
    assert r.status_code == 200, r.text
    if enabled:
        ep = r.json()
        r = admin.patch(f"/api/admin/lrs/{ep['id']}", json={"enabled": True})
        assert r.status_code == 200, r.text
    return r.json()


def _record_ids(db_exec, agent="z3-r0"):
    async def go(session):
        rows = await session.scalars(
            select(Record.id).where(Record.agent == agent).order_by(Record.id))
        return list(rows)

    return db_exec(go)


def _endpoint_row(db_exec, name):
    async def go(session):
        return (await session.scalars(
            select(LrsEndpoint).where(LrsEndpoint.name == name))).one()

    return db_exec(go)


def _forward(app, http, handler):
    async def go():
        async with app.state.sessionmaker() as session:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await lrs_service.forward_once(session, app.state.config, client)
                await session.commit()
                return result

    return http.portal.call(go)


def test_two_endpoints_receive_only_their_records(admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s1",
              scope={"types": ["communication"]})
    _endpoint(admin, "lrs-two", "https://two.example.org/", "s2",
              scope={"types": ["perception"]})
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="hello", payload={"text": "hi"})
    z3.append("perception", summary="battery", tags=["telemetry"],
              payload={"kind": "sensor", "samples": []})
    z3.append("event", summary="tick")  # in neither scope
    z3.flush()
    ids = _record_ids(db_exec)

    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200)

    assert _forward(app, http, handler) == {"forwarded": 2}

    by_host: dict[str, list] = {}
    for req in calls:
        by_host.setdefault(req.url.host, []).append(json.loads(req.content))
    assert set(by_host) == {"one.example.org", "two.example.org"}

    one = by_host["one.example.org"][0]
    assert len(one) == 1
    assert one[0]["actor"]["account"]["name"] == "z3-r0"
    assert one[0]["object"]["id"] == f"https://ltms.example.org/records/z3-r0/{ids[0]}"

    two = by_host["two.example.org"][0]
    assert len(two) == 1
    assert two[0]["object"]["id"] == f"https://ltms.example.org/records/z3-r0/{ids[1]}"
    assert "result" in two[0]  # telemetry payload was loaded and sent

    assert _endpoint_row(db_exec, "lrs-one").cursor == ids[0]
    assert _endpoint_row(db_exec, "lrs-two").cursor == ids[1]


def test_failing_endpoint_keeps_cursor_and_other_advances(admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s1",
              scope={"types": ["communication"]})
    _endpoint(admin, "lrs-two", "https://two.example.org/", "s2",
              scope={"types": ["perception"]})
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="hello")
    z3.append("perception", summary="battery", tags=["telemetry"])
    z3.flush()
    ids = _record_ids(db_exec)

    def handler(req):
        if req.url.host == "one.example.org":
            return httpx.Response(500)
        return httpx.Response(200)

    assert _forward(app, http, handler) == {"forwarded": 1}

    ep_one = _endpoint_row(db_exec, "lrs-one")
    assert ep_one.cursor == 0
    assert ep_one.last_error is not None and "HTTP 500" in ep_one.last_error
    ep_two = _endpoint_row(db_exec, "lrs-two")
    assert ep_two.cursor == ids[1]
    assert ep_two.last_error is None


def test_batch_size_limits_records_per_run(admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s1",
              scope={"types": ["communication"]})  # config fixture: lrs_batch_size=2
    z3 = mesh.member("z3-r0")
    for i in range(5):
        z3.append("communication", summary=f"m{i}")
    z3.flush()
    ids = _record_ids(db_exec)

    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200)

    totals = [_forward(app, http, handler)["forwarded"] for _ in range(4)]
    assert totals == [2, 2, 1, 0]
    assert len(calls) == 3
    assert [s["object"]["id"] for s in json.loads(calls[0].content)] == [
        f"https://ltms.example.org/records/z3-r0/{ids[0]}",
        f"https://ltms.example.org/records/z3-r0/{ids[1]}",
    ]
    assert [s["object"]["id"] for s in json.loads(calls[2].content)] == [
        f"https://ltms.example.org/records/z3-r0/{ids[4]}",
    ]
    assert _endpoint_row(db_exec, "lrs-one").cursor == ids[-1]


def test_second_forward_sends_only_new_records(admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s1",
              scope={"types": ["communication"]})
    z3 = mesh.member("z3-r0")
    for i in range(3):
        z3.append("communication", summary=f"m{i}")
    z3.flush()
    first_ids = _record_ids(db_exec)  # [1, 2, 3]

    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200)

    # batch size 2: drain in two runs
    assert _forward(app, http, handler) == {"forwarded": 2}
    assert _forward(app, http, handler) == {"forwarded": 1}
    assert [s["object"]["id"] for s in json.loads(calls[1].content)] == [
        f"https://ltms.example.org/records/z3-r0/{first_ids[2]}",
    ]

    # idempotent: nothing left to send
    before = len(calls)
    assert _forward(app, http, handler) == {"forwarded": 0}
    assert len(calls) == before

    # new records arrive: only those go out
    for i in range(2):
        z3.append("communication", summary=f"m{3 + i}")
    z3.flush()
    assert _forward(app, http, handler) == {"forwarded": 2}
    assert len(calls) == before + 1
    assert [s["object"]["id"] for s in json.loads(calls[-1].content)] == [
        f"https://ltms.example.org/records/z3-r0/{first_ids[2] + 1}",
        f"https://ltms.example.org/records/z3-r0/{first_ids[2] + 2}",
    ]
    assert _endpoint_row(db_exec, "lrs-one").cursor == first_ids[2] + 2


@pytest.mark.parametrize("status", [400, 409, 413, 422])
def test_a_permanently_rejected_batch_is_skipped_not_retried(status, admin, mesh, http, app, db_exec):
    """The LRS is optional (Sean, 9 Nov): a batch it will never accept is skipped,
    logged, and the endpoint carries on with later records."""
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s1",
              scope={"types": ["communication"]})
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="rejected")
    z3.flush()

    assert _forward(app, http, lambda req: httpx.Response(status)) == {"forwarded": 0}
    ep = _endpoint_row(db_exec, "lrs-one")
    assert ep.cursor == _record_ids(db_exec)[-1]
    assert ep.last_error is not None and str(status) in ep.last_error and "skipped" in ep.last_error

    async def skips(session):
        return list(await session.scalars(select(AuditLog).where(AuditLog.action == "lrs_skip")))
    rows = db_exec(skips)
    assert len(rows) == 1 and rows[0].detail["status"] == status and rows[0].detail["count"] == 1

    z3.append("communication", summary="accepted later")
    z3.flush()
    sent = []
    assert _forward(app, http, lambda req: sent.extend(json.loads(req.content)) or httpx.Response(200)) \
        == {"forwarded": 1}
    assert [s["object"]["definition"]["name"]["en"] for s in sent] == ["accepted later"]


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_other_failures_keep_the_cursor_for_a_retry(status, admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s1",
              scope={"types": ["communication"]})
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="hello")
    z3.flush()
    before = _endpoint_row(db_exec, "lrs-one").cursor
    assert _forward(app, http, lambda req: httpx.Response(status)) == {"forwarded": 0}
    ep = _endpoint_row(db_exec, "lrs-one")
    assert ep.cursor == before and str(status) in ep.last_error


def test_disabled_and_scopeless_endpoints_send_nothing(admin, mesh, http, app):
    _endpoint(admin, "lrs-disabled", "https://disabled.example.org/", "s1", enabled=False)
    _endpoint(admin, "lrs-blind", "https://blind.example.org/", "s2", scope=None)  # no scope: deny by default
    _endpoint(admin, "lrs-empty", "https://empty.example.org/", "s3", scope={"types": []})
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="hello")
    z3.append("perception", summary="battery", tags=["telemetry"])
    z3.flush()

    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200)

    assert _forward(app, http, handler) == {"forwarded": 0}
    assert calls == []


def test_request_carries_basic_auth_and_version_audit_has_no_secret(admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s3cret",
              scope={"types": ["communication"]})
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="hello")
    z3.flush()

    captured = {}
    def handler(req):
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.content)
        return httpx.Response(200)

    assert _forward(app, http, handler) == {"forwarded": 1}

    expected = base64.b64encode(b"alice:s3cret").decode("ascii")
    assert captured["headers"]["authorization"] == f"Basic {expected}"
    assert captured["headers"]["x-experience-api-version"] == "2.0.0"
    assert captured["headers"]["content-type"] == "application/json"
    assert len(captured["body"]) == 1

    async def go(session):
        return (await session.scalars(
            select(AuditLog).where(AuditLog.action == "lrs_export").order_by(AuditLog.id))).all()

    rows = db_exec(go)
    assert len(rows) == 1
    detail = rows[0].detail
    assert detail["endpoint"] == "lrs-one"
    assert detail["count"] == 1
    assert "s3cret" not in str(detail)
    assert "secret_enc" not in str(detail)


def test_backlog_before_and_after(admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s1",
              scope={"types": ["communication", "event"]})
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="c1")
    z3.append("perception", summary="p1")
    z3.append("event", summary="e1")
    z3.flush()

    async def go(session):
        ep = (await session.scalars(
            select(LrsEndpoint).where(LrsEndpoint.name == "lrs-one"))).one()
        return await lrs_service.backlog(session, ep)

    assert db_exec(go) == 2  # communication + event; perception is out of scope

    def handler(req):
        return httpx.Response(200)

    assert _forward(app, http, handler) == {"forwarded": 2}
    assert db_exec(go) == 0


def test_no_public_base_url_sends_nothing(app, http):
    cfg = app.state.config.model_copy(update={"public_base_url": ""})

    async def go():
        async with app.state.sessionmaker() as session:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
                return await lrs_service.forward_once(session, cfg, client)

    assert http.portal.call(go) == {"forwarded": 0}


def test_one_run_catches_up_in_several_batches_up_to_the_cap(admin, mesh, http, app, db_exec):
    # batch size 2 (fixture); 5 in-scope records -> 3 POSTs in one run
    app.state.config.lrs_max_batches_per_run = 20
    _endpoint(admin, "lrs-one", scope={"types": ["event"]})
    z3 = mesh.member("z3-r0")
    for i in range(5):
        z3.append("event", summary=f"tick {i}")
    z3.flush()
    ids = _record_ids(db_exec)
    sizes = []

    def handler(req):
        sizes.append(len(json.loads(req.content)))
        return httpx.Response(200)

    assert _forward(app, http, handler) == {"forwarded": 5}
    assert sizes == [2, 2, 1]
    assert _endpoint_row(db_exec, "lrs-one").cursor == ids[-1]

    # the cap: with at most 1 batch per run, 5 new records take 3 runs
    app.state.config.lrs_max_batches_per_run = 1
    for i in range(5):
        z3.append("event", summary=f"more {i}")
    z3.flush()
    sizes.clear()
    assert _forward(app, http, handler) == {"forwarded": 2}
    assert _forward(app, http, handler) == {"forwarded": 2}
    assert _forward(app, http, handler) == {"forwarded": 1}
    assert sizes == [2, 2, 1]


def test_every_payload_and_the_signed_header_are_sent(admin, mesh, http, app, db_exec):
    """Issue #150: anything LTMS receives, the LRS receives, and it can be re-verified."""
    app.state.config.lrs_max_batches_per_run = 20  # fixture batch size is 2
    _endpoint(admin, "lrs-all", scope={"types": ["thought", "decision", "communication"]})
    z3 = mesh.member("z3-r0")
    z3.append("thought", summary="maybe left", payload={"reasoning": "the left path is shorter"})
    z3.append("decision", summary="go left", payload={"choice": "left", "confidence": 0.8})
    z3.append("communication", summary="told the hub", payload={"text": "going left"})
    z3.flush()
    ids = _record_ids(db_exec)

    async def purge_third(session):
        await session.execute(delete(RecordPayload).where(RecordPayload.record_id == ids[2]))
        await session.commit()
    db_exec(purge_third)

    sent = []
    assert _forward(app, http, lambda req: sent.extend(json.loads(req.content)) or httpx.Response(200)) == {"forwarded": 3}
    ext = [s["context"]["extensions"] for s in sent]
    assert ext[0][EXT + "holocron/payload"] == {"reasoning": "the left path is shorter"}
    assert ext[1][EXT + "holocron/payload"] == {"choice": "left", "confidence": 0.8}
    # A purged payload is flagged and the statement is still sent.
    assert ext[2][EXT + "holocron/payload-purged"] is True
    assert EXT + "holocron/payload" not in ext[2]

    for e in ext:
        header = e[EXT + "holocron/header"]
        assert isinstance(header, str)
        assert hashlib.sha256(header.encode("utf-8")).hexdigest() == e[EXT + "holocron/hash"]
        assert json.loads(header)["payload_hash"] == e[EXT + "holocron/payload-hash"]
    for e in ext[:2]:
        assert hashlib.sha256(canonicalize(e[EXT + "holocron/payload"])).hexdigest() == e[EXT + "holocron/payload-hash"]


# --- cause and effect: StatementRef (issue #180) ---

def _statement_ref(st):
    return (st["context"].get("statement") or {}).get("id")


def test_a_decision_links_to_the_perception_that_caused_it(admin, mesh, http, app, db_exec):
    app.state.config.lrs_max_batches_per_run = 20
    _endpoint(admin, "lrs-all", scope={"types": ["perception", "decision"]})
    z3 = mesh.member("z3-r0")
    seen = z3.append("perception", summary="wall ahead", payload={"distance_m": 0.3})
    z3.append("decision", summary="stop", payload={"choice": "stop"},
              refs={"because": f"z3-r0:{seen['seq']}"})
    z3.flush()

    sent = []
    assert _forward(app, http, lambda req: sent.extend(json.loads(req.content)) or httpx.Response(200)) == {"forwarded": 2}
    perception, decision = sent
    assert _statement_ref(decision) == perception["id"]
    assert decision["context"]["statement"]["objectType"] == "StatementRef"
    assert decision["context"]["extensions"][EXT + "holocron/refs"] == {"because": f"z3-r0:{seen['seq']}"}
    assert "statement" not in perception["context"]


def test_a_deleted_cause_still_resolves_through_its_tombstone(admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-dec", scope={"types": ["decision"]})
    z3 = mesh.member("z3-r0")
    seen = z3.append("perception", summary="wall ahead", payload={"distance_m": 0.3})
    z3.append("decision", summary="stop", refs={"because": f"z3-r0:{seen['seq']}"})
    z3.flush()
    r = admin.request("DELETE", f"/api/admin/records/z3-r0/{seen['seq']}", json={"reason": "test"})
    assert r.status_code == 200, r.text

    sent = []
    _forward(app, http, lambda req: sent.extend(json.loads(req.content)) or httpx.Response(200))
    assert len(sent) == 1
    assert _statement_ref(sent[0]) == str(uuid.uuid5(NAMESPACE, seen["hash"]))


def test_causes_are_looked_up_once_per_batch(admin, mesh, http, app, db_exec):
    app.state.config.lrs_batch_size = 50
    _endpoint(admin, "lrs-dec", scope={"types": ["decision"]})
    z3 = mesh.member("z3-r0")
    causes = [z3.append("perception", summary=f"p{i}") for i in range(50)]
    for c in causes:
        z3.append("decision", summary="d", refs={"because": f"z3-r0:{c['seq']}"})
    z3.flush()

    lookups = []
    def count(conn, cursor, statement, params, context, executemany):
        if "record_tombstones" in statement and "UNION" in statement.upper():
            lookups.append(statement)
    engine = app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        sent = []
        assert _forward(app, http, lambda req: sent.extend(json.loads(req.content)) or httpx.Response(200)) == {"forwarded": 50}
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(lookups) == 1
    assert [_statement_ref(s) for s in sent] == [str(uuid.uuid5(NAMESPACE, c["hash"])) for c in causes]
