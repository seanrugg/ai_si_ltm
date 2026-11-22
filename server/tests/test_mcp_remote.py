"""Remote MCP read-only endpoint at /mcp (issue #107).

Runs against SQLite in memory by default, or PostgreSQL when
``LTMS_TEST_DATABASE_URL`` is set (each test gets a fresh schema via the
``db_url`` fixture).
"""
from __future__ import annotations

import json
from fastapi.testclient import TestClient

from server.config import load_config
from server.main import create_app

from cli_agent.client import LTMSClient
from ltms_contract import generate_key

from server.tests.conftest import ADMIN_USER, ADMIN_PASS, AdminClient, Mesh


def _make_app(db_url, **overrides):
    cfg = load_config(
        database_url=db_url,
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        cookie_secure=False,
        enable_scheduler=False,
        ingest_rate=1000,
        recall_rate=1000,
        schema_mode="create",
        **overrides,
    )
    app = create_app(cfg)
    http = TestClient(app, base_url="http://testserver")
    http.__enter__()  # run lifespan so app.state.sessionmaker and the MCP mount are ready
    admin = AdminClient(http)
    assert admin.login().status_code == 200
    return http, admin, app


def _rpc(http, method, params=None, *, host="testserver", token=None, session_id=None):
    headers = {"Host": host}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return http.post("/mcp/", json=body, headers=headers)


def _initialize(http, host="testserver"):
    return _rpc(
        http,
        "initialize",
        {"protocolVersion": "2025-06-12", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        host=host,
    )


def _extract_result(response):
    """Unwrap a JSON-RPC response into its result, surfacing errors.

    The SDK serialises list-returning tools into ``structuredContent.result`` and
    scalar-returning tools into ``content[0].text`` (a JSON string); handle both.
    """
    body = response.json()
    if "error" in body:
        return {"error": body["error"]}
    result = body.get("result")
    if isinstance(result, dict):
        if "structuredContent" in result:
            sc = result["structuredContent"]
            if isinstance(sc, dict) and "result" in sc:
                return sc["result"]
            return sc
        if result.get("content"):
            return json.loads(result["content"][0]["text"])
    return result


# --------------------------------------------------------------------------- #
# 1. Mode off: read access is denied
# --------------------------------------------------------------------------- #
def test_mode_off_denies_search(db_url):
    http, _admin, _app = _make_app(db_url, mcp_read_mode="off")
    _initialize(http)

    response = _rpc(http, "tools/call", {"name": "search_records", "arguments": {}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" in body, body
    assert "result" not in body


# --------------------------------------------------------------------------- #
# 2. Mode open: open scope applies, payloads off, results carry iri + hash
# --------------------------------------------------------------------------- #
def test_open_mode_only_scoped_types(db_url):
    http, admin, _app = _make_app(
        db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"]}
    )
    mesh = Mesh(http, admin)
    agent = mesh.member("agent-x")
    agent.append("event", summary="event one")
    agent.append("thought", summary="thought one")
    agent.flush()

    _initialize(http)
    response = _rpc(http, "tools/call", {"name": "search_records", "arguments": {}})
    assert response.status_code == 200, response.text
    records = _extract_result(response)
    assert isinstance(records, list)
    assert records, "expected at least the event record"
    assert all(rec["type"] == "event" for rec in records)
    assert any(rec["summary"] == "event one" for rec in records)
    assert not any(rec["summary"] == "thought one" for rec in records)
    for rec in records:
        assert "payload" not in rec
        assert "iri" in rec
        assert "hash" in rec
        assert rec["iri"].endswith(f"/records/{agent.name}/{rec['seq']}")


# --------------------------------------------------------------------------- #
# 3. Token with payloads: true -> payloads included
# --------------------------------------------------------------------------- #
def test_token_includes_payloads(db_url):
    http, admin, _app = _make_app(db_url, mcp_read_mode="token")
    created = admin.post(
        "/api/admin/mcp/tokens",
        json={"name": "reader", "scope": {"types": ["event"], "payloads": True}},
    )
    assert created.status_code == 200, created.text
    plaintext = created.json()["token"]

    mesh = Mesh(http, admin)
    agent = mesh.member("agent-x")
    agent.append("event", summary="payloaded event", payload={"temp": 21.5})
    agent.append("event", summary="plain event")
    agent.flush()

    _initialize(http)
    response = _rpc(
        http,
        "tools/call",
        {"name": "search_records", "arguments": {}},
        token=plaintext,
    )
    assert response.status_code == 200, response.text
    records = _extract_result(response)
    assert isinstance(records, list) and records
    for rec in records:
        assert "payload" in rec, rec
    assert any(rec["summary"] == "payloaded event" and rec["payload"] == {"temp": 21.5} for rec in records)


# --------------------------------------------------------------------------- #
# 4. get_record on a real record -> verified: true
# --------------------------------------------------------------------------- #
def test_get_record_verified(db_url):
    http, admin, _app = _make_app(db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"]})
    mesh = Mesh(http, admin)
    agent = mesh.member("agent-x")
    agent.append("event", summary="to verify")
    agent.flush()

    _initialize(http)
    response = _rpc(
        http,
        "tools/call",
        {"name": "get_record", "arguments": {"agent": agent.name, "seq": 1}},
    )
    assert response.status_code == 200, response.text
    rec = _extract_result(response)
    assert isinstance(rec, dict)
    assert rec["verified"] is True
    assert rec["hash"]
    assert rec["iri"].endswith(f"/records/{agent.name}/1")


# --------------------------------------------------------------------------- #
# 5. list_members: no public_key, permissions, scope
# --------------------------------------------------------------------------- #
def test_list_members_no_sensitive_fields(db_url):
    http, admin, _app = _make_app(db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"]})
    mesh = Mesh(http, admin)
    mesh.member("agent-x")

    _initialize(http)
    response = _rpc(http, "tools/call", {"name": "list_members", "arguments": {}})
    assert response.status_code == 200, response.text
    members = _extract_result(response)
    assert isinstance(members, list) and members
    for m in members:
        assert "public_key" not in m
        assert "permissions" not in m
        assert "scope" not in m
        assert set(m) <= {"name", "kind", "status", "swarm"}


# --------------------------------------------------------------------------- #
# 6. limit capped at 100
# --------------------------------------------------------------------------- #
def test_limit_capped_at_100(db_url):
    http, admin, _app = _make_app(db_url, mcp_read_mode="open")
    mesh = Mesh(http, admin)
    agent = mesh.member("agent-x")
    for i in range(10):
        agent.append("event", summary=f"event {i}")
    agent.flush()

    _initialize(http)
    response = _rpc(
        http,
        "tools/call",
        {"name": "search_records", "arguments": {"limit": 500}},
    )
    assert response.status_code == 200, response.text
    records = _extract_result(response)
    assert isinstance(records, list)
    assert len(records) <= 100


# --------------------------------------------------------------------------- #
# 7. Host header validation (transport security)
# --------------------------------------------------------------------------- #
def test_host_localhost_with_port_succeeds(db_url):
    http, admin, _app = _make_app(db_url, mcp_read_mode="open")
    _initialize(http, host="localhost:8080")
    response = _rpc(
        http, "tools/call", {"name": "search_records", "arguments": {}}, host="localhost:8080"
    )
    assert response.status_code == 200, response.text
    assert "error" not in response.json()


def test_host_evil_rejected(db_url):
    http, _admin, _app = _make_app(db_url, mcp_read_mode="open")
    _initialize(http)
    response = _rpc(
        http, "tools/call", {"name": "search_records", "arguments": {}}, host="evil.example"
    )
    assert response.status_code == 421, response.text


# --------------------------------------------------------------------------- #
# Scope applies to every tool, not just search (review of #112)
# --------------------------------------------------------------------------- #
def test_get_record_outside_scope_is_not_found(db_url):
    # Open scope sees events only; guessing agent + seq of a thought must not
    # return it, and must look exactly like a missing record.
    http, admin, _app = _make_app(db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"]})
    mesh = Mesh(http, admin)
    a = mesh.member("agent-y")
    a.append("event", summary="visible")   # seq 1
    a.append("thought", summary="private")  # seq 2
    a.flush()
    _initialize(http)

    ok = _extract_result(_rpc(http, "tools/call", {"name": "get_record", "arguments": {"agent": "agent-y", "seq": 1}}))
    assert ok.get("summary") == "visible", ok

    hidden = _rpc(http, "tools/call", {"name": "get_record", "arguments": {"agent": "agent-y", "seq": 2}}).json()
    missing = _rpc(http, "tools/call", {"name": "get_record", "arguments": {"agent": "agent-y", "seq": 99}}).json()
    assert "private" not in json.dumps(hidden)
    # same shape of answer for out-of-scope and missing
    assert ("error" in hidden) == ("error" in missing)
    assert ("result" in hidden and hidden["result"].get("isError")) == ("result" in missing and missing["result"].get("isError"))


def test_directory_tools_hide_members_outside_scope(db_url):
    # An open scope limited to swarm "public-swarm" must not reveal other
    # members' names (e.g. a private swarm or free agents).
    http, admin, _app = _make_app(
        db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"], "swarms": ["public-swarm"]}
    )
    mesh = Mesh(http, admin)
    mesh.member("free-agent-secret")
    _initialize(http)

    members = _extract_result(_rpc(http, "tools/call", {"name": "list_members", "arguments": {}}))
    assert isinstance(members, list)
    assert all(m["name"] != "free-agent-secret" for m in members), members
    report = _extract_result(_rpc(http, "tools/call", {"name": "integrity_report", "arguments": {}}))
    assert all(r["member"] != "free-agent-secret" for r in report), report


def test_bare_mcp_path_works_with_dashboard_mounted(db_url, tmp_path):
    """``/mcp`` (no slash) reaches MCP directly, even with the dashboard's
    catch-all mounted, and without a redirect."""
    (tmp_path / "index.html").write_text("<!doctype html>")
    http, _admin, _app = _make_app(db_url, mcp_read_mode="open", dashboard_dir=str(tmp_path))
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-12", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "1"}}}
    r = http.post("/mcp", json=body, headers={"Host": "testserver"}, follow_redirects=False)
    assert r.status_code == 200, r.text
    assert "result" in r.json()
    # The dashboard still serves everything else.
    assert http.get("/").status_code == 200


# --------------------------------------------------------------------------- #
# #443: stored integrity results, open budget, actionable and logged 429s
# --------------------------------------------------------------------------- #
import logging  # noqa: E402

from server import mcp_remote  # noqa: E402
from server.services import integrity as integrity_service  # noqa: E402


def _call(http, tool="list_members", ip=None, token=None, arguments=None):
    if ip is not None:
        http._transport.client = (ip, 50000)
    return _rpc(http, "tools/call", {"name": tool, "arguments": arguments or {}}, token=token).json()


def _deny_n(http, n, **kw):
    """Call until ``n`` calls were refused (the bucket refills 1/s, so a slow run
    lets the odd call through); returns the refusals' data."""
    refused = []
    for _ in range(n + 400):
        body = _call(http, **kw)
        if "error" in body:
            refused.append(body["error"]["data"])
            if len(refused) == n:
                return refused
    raise AssertionError(f"only {len(refused)} of {n} calls were refused")


def _audit(admin):
    return admin.get("/api/admin/recall-log", params={"action": "mcp_rate_limited"}).json()["entries"]


def test_integrity_report_reads_stored_results_only(db_url, monkeypatch):
    http, admin, _app = _make_app(db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"]})
    mesh = Mesh(http, admin)
    checked = mesh.member("checked")
    checked.append("event", summary="one")
    checked.flush()
    assert admin.get("/api/admin/integrity", params={"member": "checked"}).status_code == 200
    mesh.member("never-checked")

    def boom(*a, **k):
        raise AssertionError("integrity_report must not verify on demand")
    monkeypatch.setattr(integrity_service, "verify_all", boom)
    monkeypatch.setattr(integrity_service, "verify_member", boom)
    monkeypatch.setattr(integrity_service, "check_all", boom)
    monkeypatch.setattr(integrity_service, "check_member", boom)

    _initialize(http)
    report = {r["member"]: r for r in _extract_result(_rpc(http, "tools/call", {"name": "integrity_report", "arguments": {}}))}
    assert report["checked"]["status"] == "ok" and report["checked"]["records"] == 1
    assert report["checked"]["verified_at"] and report["checked"]["verified_through"] == 1
    assert report["never-checked"]["status"] == "not_yet_verified" and report["never-checked"]["verified_at"] is None


def test_open_budget_is_shared_by_tokenless_callers_not_tokens(db_url):
    http, admin, _app = _make_app(db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"]},
                                  mcp_open_budget=5)
    plaintext = admin.post("/api/admin/mcp/tokens", json={"name": "reader", "scope": {"types": ["event"]}}).json()["token"]
    _initialize(http)
    for i in range(5):
        assert "error" not in _call(http, ip=f"10.0.0.{i + 1}"), i
    denied = _call(http, ip="10.0.0.6")["error"]  # a different IP, but the shared budget is spent
    assert denied["code"] == 429 and denied["message"] == "rate_limited"
    data = denied["data"]
    assert data["reason"] == "open_budget" and data["retry_after_seconds"] >= 1
    assert data["message"] == (f"Open read access on this FLTMS instance is busy (shared limit of 5 calls/min reached). "
                               f"Retry in {data['retry_after_seconds']} s, or ask the fleet admin for a read token.")
    assert "10.0.0" not in json.dumps(data)  # nothing about other callers
    assert "error" not in _call(http, ip="10.0.0.6", token=plaintext)  # a token caller is unaffected
    assert admin.get("/api/admin/mcp").json()["open_budget"] == 5


def test_per_caller_429_says_when_to_retry(db_url):
    http, _admin, _app = _make_app(db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"]},
                                   mcp_open_budget=0)
    _initialize(http)
    for _ in range(mcp_remote.CALLER_LIMIT):
        assert "error" not in _call(http, ip="10.0.0.1")
    data = _call(http, ip="10.0.0.1")["error"]["data"]
    assert data["reason"] == "per_caller" and data["retry_after_seconds"] >= 1
    assert data["message"] == (f"Rate limited: this caller is limited to {mcp_remote.CALLER_LIMIT} tool calls per minute. "
                               f"Retry in {data['retry_after_seconds']} s.")
    assert "error" not in _call(http, ip="10.0.0.2")  # another caller has its own bucket


def test_a_hundred_denials_make_one_audit_row_and_one_log_line(db_url, caplog):
    http, admin, app = _make_app(db_url, mcp_read_mode="open", mcp_open_scope={"types": ["event"]},
                                 mcp_open_budget=0)
    _initialize(http)
    denials = app.state.mcp_denials
    start = mcp_remote.DenialLog.window_of(denials.clock()) + 1  # pin one minute for the test
    denials.clock = lambda: start
    assert all(d["reason"] == "per_caller" for d in _deny_n(http, 100, ip="10.9.9.9"))
    assert _audit(admin) == []  # nothing written while the minute is open

    denials.clock = lambda: start + 60  # the next minute: the scheduler writes the ended one
    with caplog.at_level(logging.WARNING, logger="ltms.mcp"):
        async def flush():
            async with app.state.sessionmaker() as s:
                out = await denials.flush(s)
                await s.commit()
                return out
        assert http.portal.call(flush) == {"mcp_rate_limited_rows": 1}
    rows = _audit(admin)
    assert len(rows) == 1
    assert rows[0]["detail"]["denied"] == 100 and rows[0]["detail"]["reason"] == "per_caller"
    assert rows[0]["detail"]["key_kind"] == "ip" and rows[0]["detail"]["key"] == "10.9.9.9"
    lines = [r for r in caplog.records if "MCP rate limited" in r.getMessage()]
    assert len(lines) == 1 and "denied=100" in lines[0].getMessage()
    assert admin.get("/api/admin/mcp").json()["rate_limited_24h"] == {
        "total": 100, "by_reason": {"per_caller": 100, "open_budget": 0}}


def test_token_denials_are_logged_by_name_never_the_token(db_url):
    http, admin, app = _make_app(db_url, mcp_read_mode="token")
    plaintext = admin.post("/api/admin/mcp/tokens", json={"name": "busy-agent", "scope": {"types": ["event"]}}).json()["token"]
    _initialize(http)
    denials = app.state.mcp_denials
    start = mcp_remote.DenialLog.window_of(denials.clock()) + 1
    denials.clock = lambda: start
    _deny_n(http, 3, token=plaintext)
    denials.clock = lambda: start + 60
    # The first denial of the next minute writes the ended minute.
    assert _call(http, token=plaintext)["error"]["data"]["reason"] == "per_caller"
    rows = _audit(admin)
    assert len(rows) == 1 and rows[0]["detail"]["key_kind"] == "token"
    assert rows[0]["detail"]["key"] == "busy-agent" and rows[0]["detail"]["denied"] == 3
    import hashlib
    raw = json.dumps(rows)
    assert plaintext not in raw and hashlib.sha256(plaintext.encode()).hexdigest() not in raw


def test_denial_log_caps_the_callers_it_tracks(monkeypatch):
    monkeypatch.setattr(mcp_remote, "DENIAL_KEYS_MAX", 2)
    d = mcp_remote.DenialLog(clock=lambda: 120.0)
    for ip in ("a", "b", "c", "d", "c"):
        assert d.note("per_caller", "ip", ip) == []
    d.clock = lambda: 180.0
    ended = sorted((w["key"], w["denied"]) for w in d.take_ended())
    assert ended == [("(many)", 3), ("a", 1), ("b", 1)]
    assert d.take_ended() == []


def test_a_tool_call_records_token_use(db_url):
    """#445: resolve's usage update used to be rolled back (the tools never commit)."""
    http, admin, _app = _make_app(db_url, mcp_read_mode="token")
    plaintext = admin.post("/api/admin/mcp/tokens", json={"name": "counted", "scope": {"types": ["event"]}}).json()["token"]
    _initialize(http)
    before = admin.get("/api/admin/mcp/tokens").json()["tokens"][0]
    assert before["use_count"] == 0 and before["last_used_at"] is None
    for tool in ("list_members", "search_records", "integrity_report"):
        assert "error" not in _call(http, tool=tool, token=plaintext)
    after = admin.get("/api/admin/mcp/tokens").json()["tokens"][0]
    assert after["use_count"] == 3 and after["last_used_at"] is not None
    # A refused call (here: no such record) after the checks still counts as a use.
    _call(http, tool="get_record", token=plaintext, arguments={"agent": "nobody", "seq": 1})
    assert admin.get("/api/admin/mcp/tokens").json()["tokens"][0]["use_count"] == 4


def test_rate_limited_token_calls_are_not_counted(db_url):
    http, admin, _app = _make_app(db_url, mcp_read_mode="token")
    plaintext = admin.post("/api/admin/mcp/tokens", json={"name": "busy", "scope": {"types": ["event"]}}).json()["token"]
    _initialize(http)
    ok = refused = 0
    while refused < 5:
        if "error" in _call(http, token=plaintext):
            refused += 1
        else:
            ok += 1
    assert ok >= mcp_remote.CALLER_LIMIT
    assert admin.get("/api/admin/mcp/tokens").json()["tokens"][0]["use_count"] == ok


def test_open_payloads_return_payloads_only_when_on(db_url):
    """#447: an open (no-token) request gets payloads when the open scope says so."""
    for payloads, expect in ((True, True), (False, False), (None, False)):
        scope = {"types": ["event"]}
        if payloads is not None:
            scope["payloads"] = payloads
        http, admin, _app = _make_app(db_url, mcp_read_mode="open", mcp_open_scope=scope)
        mesh = Mesh(http, admin)
        agent = mesh.member(f"agent-{str(payloads).lower()}")
        agent.append("event", summary="with details", payload={"cm": 12})
        agent.append("thought", summary="outside the open types", payload={"secret": 1})
        agent.flush()
        _initialize(http)
        records = _extract_result(_rpc(http, "tools/call", {"name": "search_records", "arguments": {}}))
        assert [r["summary"] for r in records] == ["with details"], payloads
        assert ("payload" in records[0]) is expect, (payloads, records[0])
        if expect:
            assert records[0]["payload"] == {"cm": 12}
            one = _extract_result(_rpc(http, "tools/call", {"name": "get_record",
                                                            "arguments": {"agent": agent.name, "seq": 1}}))
            assert one["payload"] == {"cm": 12}
        http.__exit__(None, None, None)


def test_token_requests_unaffected_by_open_payloads(db_url):
    http, admin, _app = _make_app(db_url, mcp_read_mode="open",
                                  mcp_open_scope={"types": ["event"], "payloads": True})
    plaintext = admin.post("/api/admin/mcp/tokens", json={"name": "headers", "scope": {"types": ["event"]}}).json()["token"]
    mesh = Mesh(http, admin)
    agent = mesh.member("agent-t")
    agent.append("event", summary="e", payload={"cm": 1})
    agent.flush()
    _initialize(http)
    records = _extract_result(_rpc(http, "tools/call", {"name": "search_records", "arguments": {}}, token=plaintext))
    assert records and "payload" not in records[0]  # the token's own scope (no payloads) applies
