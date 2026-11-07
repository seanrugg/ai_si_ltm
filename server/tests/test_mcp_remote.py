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
    http, admin, _app = _make_app(db_url, mcp_read_mode="open")
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
    http, admin, _app = _make_app(db_url, mcp_read_mode="open")
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
