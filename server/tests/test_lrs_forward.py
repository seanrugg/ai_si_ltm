"""LRS forwarder: per-endpoint background forwarding (issue #61).

No network: the LRS is stubbed with httpx.MockTransport.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from server.config import load_config
from server.models import AuditLog, LrsEndpoint, Record
from server.services import lrs as lrs_service
from server.tests.conftest import ADMIN_PASS, ADMIN_USER


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


def test_409_keeps_cursor_and_reports_mapping(admin, mesh, http, app, db_exec):
    _endpoint(admin, "lrs-one", "https://one.example.org/", "s1",
              scope={"types": ["communication"]})
    z3 = mesh.member("z3-r0")
    z3.append("communication", summary="hello")
    z3.flush()

    def handler(req):
        return httpx.Response(409)

    assert _forward(app, http, handler) == {"forwarded": 0}
    ep = _endpoint_row(db_exec, "lrs-one")
    assert ep.cursor == 0
    assert ep.last_error is not None and "409" in ep.last_error and "mapping" in ep.last_error


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
