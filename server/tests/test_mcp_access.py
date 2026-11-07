"""MCP read access: ``resolve()``, tokens and the admin API (issue #106).

Tests 1-5 run against the default test database (SQLite, or PostgreSQL when
``LTMS_TEST_DATABASE_URL`` is set). Test 6 (migration) needs PostgreSQL.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.config import load_config
from server.models import Base, McpToken
from server.services import mcp_access
from server.services.recall import Scope

PG_URL = os.environ.get("LTMS_TEST_DATABASE_URL")

needs_postgres = pytest.mark.skipif(
    not PG_URL,
    reason="migration test needs a PostgreSQL test database (LTMS_TEST_DATABASE_URL)",
)

ADMIN_USER, ADMIN_PASS = "admin", "correct horse battery staple"


def _make_config(db_url, **overrides):
    return load_config(
        database_url=db_url,
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        cookie_secure=False,
        enable_scheduler=False,
        schema_mode="create",
        **overrides,
    )


def _run_session(db_url, cfg, fn):
    """Run ``fn(session)`` against the test database (tables created fresh)."""

    async def go():
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(engine, expire_on_commit=False)
            async with sm() as session:
                result = await fn(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(go())


# --------------------------------------------------------------------------- #
# 1. Mode off: resolve -> None with or without a token.
# --------------------------------------------------------------------------- #
def test_mode_off_denies_without_token(db_url):
    cfg = _make_config(db_url, mcp_read_mode="off")
    assert _run_session(db_url, cfg, lambda s: mcp_access.resolve(s, cfg, None)) is None


def test_mode_off_denies_with_token(db_url):
    cfg = _make_config(db_url, mcp_read_mode="off")
    assert _run_session(db_url, cfg, lambda s: mcp_access.resolve(s, cfg, "ltms_anything")) is None


# --------------------------------------------------------------------------- #
# 2. Mode open, no token -> open scope; payloads always off.
# --------------------------------------------------------------------------- #
def test_mode_open_no_token_grants_open_scope(db_url):
    cfg = _make_config(
        db_url,
        mcp_read_mode="open",
        mcp_open_scope={"types": ["thought", "lesson"], "swarms": None, "payloads": True},
    )
    scope = _run_session(db_url, cfg, lambda s: mcp_access.resolve(s, cfg, None))
    assert scope == Scope(types=["thought", "lesson"], agents=None, places=None, swarms=None)


# --------------------------------------------------------------------------- #
# 3. Mode open, wrong token -> None (no fallback to open).
# --------------------------------------------------------------------------- #
def test_mode_open_wrong_token_denies(db_url):
    cfg = _make_config(db_url, mcp_read_mode="open")
    assert _run_session(db_url, cfg, lambda s: mcp_access.resolve(s, cfg, "ltms_wrong")) is None


# --------------------------------------------------------------------------- #
# 4. Mode token: valid token -> its scope; revoked -> None; expired -> None.
# --------------------------------------------------------------------------- #
def test_mode_token_valid_revoked_expired(db_url):
    cfg = _make_config(db_url, mcp_read_mode="token")

    async def go():
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(engine, expire_on_commit=False)
            async with sm() as session:
                row, plaintext = await mcp_access.create(
                    session, name="reader", scope={"types": ["thought"]},
                    expires_at=None, actor="admin:admin",
                )
                await session.commit()

                expected = Scope(types=["thought"], agents=None, places=None, swarms=None)
                assert (await mcp_access.resolve(session, cfg, plaintext)) == expected

                await mcp_access.revoke(session, row.id, actor="admin:admin")
                await session.commit()
                assert await mcp_access.resolve(session, cfg, plaintext) is None

                expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                row2, plaintext2 = await mcp_access.create(
                    session, name="ex", scope={"types": ["thought"]},
                    expires_at=expired_at, actor="admin:admin",
                )
                await session.commit()
                assert await mcp_access.resolve(session, cfg, plaintext2) is None
        finally:
            await engine.dispose()

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# include_payloads
# --------------------------------------------------------------------------- #
def test_include_payloads_only_for_token_scope():
    assert mcp_access.include_payloads({"types": ["thought"], "payloads": True}) is True
    assert mcp_access.include_payloads({"types": ["thought"], "payloads": False}) is False
    assert mcp_access.include_payloads({"types": ["thought"]}) is False
    assert mcp_access.include_payloads(None) is False
    assert mcp_access.include_payloads({}) is False


# --------------------------------------------------------------------------- #
# 5. Create via the API: plaintext returned once; GET never contains it or its
#    hash; audit row has no token.
# --------------------------------------------------------------------------- #
def test_create_via_api_returns_plaintext_once(http, admin):
    r = admin.post(
        "/api/admin/mcp/tokens",
        json={"name": "reader", "scope": {"types": ["thought"]}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["token"]
    assert token.startswith("ltms_")
    assert body["name"] == "reader"
    assert "id" in body

    # GET /api/admin/mcp never contains the plaintext or the hash.
    g = admin.get("/api/admin/mcp")
    assert g.status_code == 200, g.text
    payload = g.json()
    rendered = str(payload)
    assert token not in rendered
    assert hashlib.sha256(token.encode()).hexdigest() not in rendered
    for t in payload["tokens"]:
        assert "token_hash" not in t
        if t["name"] == "reader":
            assert t["scope"] == {"types": ["thought"]}

    # The audit row records name and scope, never the token.
    a = admin.get("/api/admin/recall-log?action=mcp_token_create")
    assert a.status_code == 200, a.text
    entries = a.json()["entries"]
    create_entries = [e for e in entries if e["action"] == "mcp_token_create"]
    assert create_entries, "expected an mcp_token_create audit row"
    for e in create_entries:
        assert "token" not in e["detail"]
        assert "token_hash" not in e["detail"]
        assert e["detail"]["name"] == "reader"


# --------------------------------------------------------------------------- #
# 6. Migration (PostgreSQL): upgrade to head creates mcp_tokens.
# --------------------------------------------------------------------------- #
@needs_postgres
def test_migration_creates_mcp_tokens(db_url):
    from io import StringIO
    from contextlib import redirect_stderr, redirect_stdout

    from server.migrate import main

    def invoke(argv):
        out, err = StringIO(), StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                main([*argv, "--url", db_url])
        except SystemExit as exc:
            code = 0 if exc.code is None else int(exc.code)
        return code, out.getvalue(), err.getvalue()

    code, _, err = invoke(["upgrade"])
    assert code == 0, err

    async def table_exists():
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                row = await conn.execute(
                    text("SELECT 1 FROM information_schema.tables WHERE table_name = 'mcp_tokens'")
                )
                return row.first() is not None
        finally:
            await engine.dispose()

    assert asyncio.run(table_exists()), "mcp_tokens table missing after upgrade to head"



def test_duplicate_token_name_is_409(admin):
    assert admin.post("/api/admin/mcp/tokens", json={"name": "dup", "scope": {"types": ["event"]}}).status_code == 200
    r = admin.post("/api/admin/mcp/tokens", json={"name": "dup", "scope": {"types": ["event"]}})
    assert r.status_code == 409, r.text
