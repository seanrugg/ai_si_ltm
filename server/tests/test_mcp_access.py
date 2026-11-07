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


# --------------------------------------------------------------------------- #
# 7. Token metadata: owner, purpose, use_count and stale (issue #117).
# --------------------------------------------------------------------------- #
def test_create_with_owner_and_purpose(http, admin):
    r = admin.post(
        "/api/admin/mcp/tokens",
        json={"name": "meta", "scope": {"types": ["thought"]},
              "owner": "Sean (Claude connector)",
              "purpose": "Daily read of robot outcomes"},
    )
    assert r.status_code == 200, r.text
    g = admin.get("/api/admin/mcp")
    assert g.status_code == 200, g.text
    tokens = g.json()["tokens"]
    t = next(t for t in tokens if t["name"] == "meta")
    assert t["owner"] == "Sean (Claude connector)"
    assert t["purpose"] == "Daily read of robot outcomes"
    assert t["use_count"] == 0
    # The audit row records owner and purpose, never the token or its hash.
    a = admin.get("/api/admin/recall-log?action=mcp_token_create")
    assert a.status_code == 200, a.text
    entries = [e for e in a.json()["entries"] if e["action"] == "mcp_token_create" and e["detail"].get("name") == "meta"]
    assert entries, "expected an mcp_token_create audit row for meta"
    for e in entries:
        assert e["detail"]["owner"] == "Sean (Claude connector)"
        assert e["detail"]["purpose"] == "Daily read of robot outcomes"
        assert "token" not in e["detail"]
        assert "token_hash" not in e["detail"]


def test_create_owner_purpose_over_limit_422(admin):
    assert admin.post("/api/admin/mcp/tokens", json={"name": "lim", "scope": {}}).status_code == 200
    r = admin.post("/api/admin/mcp/tokens", json={
        "name": "lim", "owner": "x" * 129, "purpose": "y" * 501,
    })
    assert r.status_code == 422, r.text


def test_resolve_increments_use_count(db_url):
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
                for _ in range(3):
                    assert (await mcp_access.resolve(session, cfg, plaintext)) is not None
                await session.commit()

                from sqlalchemy import select as sa_select
                got = await session.scalar(
                    sa_select(McpToken.use_count).where(McpToken.id == row.id)
                )
                assert got == 3
        finally:
            await engine.dispose()

    asyncio.run(go())


def test_resolve_denied_never_increments(db_url):
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
                assert await mcp_access.resolve(session, cfg, "ltms_wrong") is None
                assert await mcp_access.resolve(session, cfg, "") is None
                # A valid read still increments once.
                assert (await mcp_access.resolve(session, cfg, plaintext)) is not None
                await session.commit()

                from sqlalchemy import select as sa_select
                got = await session.scalar(
                    sa_select(McpToken.use_count).where(McpToken.id == row.id)
                )
                assert got == 1
        finally:
            await engine.dispose()

    asyncio.run(go())


def test_stale_flag(db_url):
    from server.api.admin import _is_stale
    now = datetime.now(timezone.utc)

    async def go():
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(engine, expire_on_commit=False)
            async with sm() as session:
                used_91, _ = await mcp_access.create(
                    session, name="stale-91", scope={}, expires_at=None, actor="admin:admin",
                )
                used_91.last_used_at = now - timedelta(days=91)
                never_91, _ = await mcp_access.create(
                    session, name="stale-never", scope={}, expires_at=None, actor="admin:admin",
                )
                never_91.created_at = now - timedelta(days=91)
                used_89, _ = await mcp_access.create(
                    session, name="fresh-89", scope={}, expires_at=None, actor="admin:admin",
                )
                used_89.last_used_at = now - timedelta(days=89)
                revoked, _ = await mcp_access.create(
                    session, name="revoked", scope={}, expires_at=None, actor="admin:admin",
                )
                revoked.revoked_at = now
                await session.commit()

                assert _is_stale(used_91) is True
                assert _is_stale(never_91) is True
                assert _is_stale(used_89) is False
                assert _is_stale(revoked) is False
        finally:
            await engine.dispose()

    asyncio.run(go())
