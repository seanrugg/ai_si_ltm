"""MCP read mode and open scope manageable from the dashboard (issue #120).

Covers the database-backed runtime_settings store, effective-setting precedence
(DB over environment), the lock flag, confirm-required for widening public
access, audit rows, and the no-restart behavior of resolve().
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.config import load_config
from server.main import create_app
from server.models import Base
from server.services import mcp_access, runtime_settings
from server.services.recall import Scope

from .conftest import ADMIN_USER, ADMIN_PASS, AdminClient

PG_URL = os.environ.get("LTMS_TEST_DATABASE_URL")

needs_postgres = pytest.mark.skipif(
    not PG_URL,
    reason="migration test needs a PostgreSQL test database (LTMS_TEST_DATABASE_URL)",
)


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
# GET /admin/mcp: environment values when no DB row
# --------------------------------------------------------------------------- #
def test_get_mcp_environment_source(admin):
    g = admin.get("/api/admin/mcp")
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["mode"] == "off"
    assert body["open_scope"] == {"types": [], "swarms": None}
    assert body["source"] == "environment"
    assert body["locked"] is False
    assert body["updated_at"] is None
    assert body["updated_by"] is None


# --------------------------------------------------------------------------- #
# DB row wins over environment
# --------------------------------------------------------------------------- #
def test_db_row_wins_over_environment(db_url, admin):
    cfg = _make_config(db_url, mcp_read_mode="off", mcp_open_scope={"types": [], "swarms": None})

    async def go():
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(engine, expire_on_commit=False)
            async with sm() as session:
                await runtime_settings.set_setting(
                    session, runtime_settings.MCP_ACCESS_KEY,
                    {"mode": "open", "open_scope": {"types": ["thought"], "swarms": None}},
                    updated_by="admin:admin",
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(go())

    g = admin.get("/api/admin/mcp")
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["mode"] == "open"
    assert body["open_scope"] == {"types": ["thought"], "swarms": None}
    assert body["source"] == "dashboard"
    assert body["updated_by"] == "admin:admin"


# --------------------------------------------------------------------------- #
# PUT /admin/mcp/settings
# --------------------------------------------------------------------------- #
def test_put_open_without_confirm_is_422(admin):
    r = admin.put("/api/admin/mcp/settings", json={"mode": "open"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "confirm_required"


def test_put_open_with_confirm_is_200(admin):
    r = admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought"], "swarms": None},
        "confirm": "open",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "open"
    assert body["open_scope"] == {"types": ["thought"], "swarms": None}
    assert body["source"] == "dashboard"

    # GET now reports the dashboard value.
    g = admin.get("/api/admin/mcp")
    assert g.status_code == 200, g.text
    assert g.json()["source"] == "dashboard"
    assert g.json()["mode"] == "open"


def test_put_invalid_mode_is_422(admin):
    r = admin.put("/api/admin/mcp/settings", json={"mode": "bogus", "confirm": "open"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "bad_mode"


def test_put_invalid_type_is_422(admin):
    r = admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["not_a_type"]}, "confirm": "open",
    })
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "bad_scope"


def test_put_widening_scope_without_confirm_is_422(admin):
    # First switch to open with a narrow scope.
    admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought"], "swarms": None},
        "confirm": "open",
    })
    # Now widen it (add a type) without confirm -> 422.
    r = admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought", "lesson"], "swarms": None},
    })
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "confirm_required"


def test_put_widening_scope_with_confirm_is_200(admin):
    admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought"], "swarms": None},
        "confirm": "open",
    })
    r = admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought", "lesson"], "swarms": None},
        "confirm": "open",
    })
    assert r.status_code == 200, r.text
    assert r.json()["open_scope"] == {"types": ["thought", "lesson"], "swarms": None}


def test_put_switch_open_to_off_no_confirm(admin):
    # Switch to open.
    admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought"], "swarms": None},
        "confirm": "open",
    })
    # Switch back to off: narrowing, no confirm needed.
    r = admin.put("/api/admin/mcp/settings", json={"mode": "off"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "off"


def test_put_payloads_stripped_from_open_scope(admin):
    r = admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought"], "payloads": True},
        "confirm": "open",
    })
    assert r.status_code == 200, r.text
    assert r.json()["open_scope"] == {"types": ["thought"], "swarms": None}


# --------------------------------------------------------------------------- #
# DELETE /admin/mcp/settings restores environment default
# --------------------------------------------------------------------------- #
def test_delete_restores_environment(admin):
    admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought"], "swarms": None},
        "confirm": "open",
    })
    r = admin.delete("/api/admin/mcp/settings")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "environment"

    g = admin.get("/api/admin/mcp")
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["mode"] == "off"
    assert body["source"] == "environment"
    assert body["updated_at"] is None


# --------------------------------------------------------------------------- #
# Audit rows
# --------------------------------------------------------------------------- #
def test_audit_rows_written(admin):
    admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought"], "swarms": None},
        "confirm": "open",
    })
    a = admin.get("/api/admin/recall-log?action=mcp_settings_change")
    assert a.status_code == 200, a.text
    entries = a.json()["entries"]
    assert entries, "expected an mcp_settings_change audit row"
    e = entries[0]
    assert e["detail"]["old"]["mode"] == "off"
    assert e["detail"]["new"]["mode"] == "open"
    assert e["detail"]["new"]["open_scope"] == {"types": ["thought"], "swarms": None}

    admin.delete("/api/admin/mcp/settings")
    a = admin.get("/api/admin/recall-log?action=mcp_settings_reset")
    assert a.status_code == 200, a.text
    entries = a.json()["entries"]
    assert entries, "expected an mcp_settings_reset audit row"
    assert entries[0]["detail"]["old"]["mode"] == "open"


# --------------------------------------------------------------------------- #
# Locked: environment wins, PUT -> 409
# --------------------------------------------------------------------------- #
def test_locked_put_is_409(db_url):
    from fastapi.testclient import TestClient

    cfg = _make_config(db_url, mcp_read_mode="off", mcp_settings_locked=True)
    app = create_app(config=cfg)
    with TestClient(app, base_url="http://testserver") as http:
        client = AdminClient(http)
        assert client.login().status_code == 200
        r = client.put("/api/admin/mcp/settings", json={
            "mode": "open", "open_scope": {"types": ["thought"], "swarms": None},
            "confirm": "open",
        })
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "locked"


# --------------------------------------------------------------------------- #
# No-restart: resolve reads DB on every call
# --------------------------------------------------------------------------- #
def test_resolve_reads_db_each_call(db_url):
    cfg = _make_config(db_url, mcp_read_mode="off")

    async def go():
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(engine, expire_on_commit=False)
            async with sm() as session:
                # No row: environment (off) applies.
                assert (await mcp_access.resolve(session, cfg, None)) is None

                # Write open to the DB.
                await runtime_settings.set_setting(
                    session, runtime_settings.MCP_ACCESS_KEY,
                    {"mode": "open", "open_scope": {"types": ["thought"], "swarms": None}},
                    updated_by="admin:admin",
                )
                await session.commit()

                # Same config object, no restart: next resolve sees the DB row.
                scope = await mcp_access.resolve(session, cfg, None)
                assert scope == Scope(types=["thought"], agents=None, places=None, swarms=None)

                # Switch back to off in the DB.
                await runtime_settings.set_setting(
                    session, runtime_settings.MCP_ACCESS_KEY,
                    {"mode": "off", "open_scope": {"types": [], "swarms": None}},
                    updated_by="admin:admin",
                )
                await session.commit()
                assert (await mcp_access.resolve(session, cfg, None)) is None
        finally:
            await engine.dispose()

    asyncio.run(go())


def test_resolve_locked_ignores_db(db_url):
    cfg = _make_config(db_url, mcp_read_mode="off", mcp_settings_locked=True)

    async def go():
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(engine, expire_on_commit=False)
            async with sm() as session:
                # Write open to the DB, but locked -> environment wins.
                await runtime_settings.set_setting(
                    session, runtime_settings.MCP_ACCESS_KEY,
                    {"mode": "open", "open_scope": {"types": ["thought"], "swarms": None}},
                    updated_by="admin:admin",
                )
                await session.commit()
                assert (await mcp_access.resolve(session, cfg, None)) is None
        finally:
            await engine.dispose()

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# Migration (PostgreSQL): upgrade creates runtime_settings
# --------------------------------------------------------------------------- #
@needs_postgres
def test_migration_creates_runtime_settings(db_url):
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

    from sqlalchemy import text

    async def table_exists():
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                row = await conn.execute(
                    text("SELECT 1 FROM information_schema.tables WHERE table_name = 'runtime_settings'")
                )
                return row.first() is not None
        finally:
            await engine.dispose()

    assert asyncio.run(table_exists()), "runtime_settings table missing after upgrade to head"
