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
def test_db_row_wins_over_environment(admin, db_exec):
    # Write through the app's own session: with SQLite the test database is
    # in-memory, so a separate engine would see a different, empty database.
    async def put_row(session):
        await runtime_settings.set_setting(
            session, runtime_settings.MCP_ACCESS_KEY,
            {"mode": "open", "open_scope": {"types": ["thought"], "swarms": None}},
            updated_by="admin:admin",
        )

    db_exec(put_row)

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


def test_put_open_payloads_kept(admin):
    # #447 (Sean, 18 Nov): the open scope may include payloads; before, they were stripped.
    r = admin.put("/api/admin/mcp/settings", json={
        "mode": "open", "open_scope": {"types": ["thought"], "payloads": True},
        "confirm": "open",
    })
    assert r.status_code == 200, r.text
    assert r.json()["open_scope"] == {"types": ["thought"], "swarms": None, "payloads": True}


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


def test_updated_at_moves_on_each_change(admin):
    body = {"mode": "token", "open_scope": {"types": ["thought"], "swarms": None}}
    assert admin.put("/api/admin/mcp/settings", json=body).status_code == 200
    first = admin.get("/api/admin/mcp").json()["updated_at"]
    import time
    time.sleep(1.1)
    body["mode"] = "off"
    assert admin.put("/api/admin/mcp/settings", json=body).status_code == 200
    second = admin.get("/api/admin/mcp").json()["updated_at"]
    assert first and second and second > first


def test_open_scope_swarms_must_be_names_or_null(admin):
    for bad in (["ok", 3], [""], ["x" * 65]):
        r = admin.put("/api/admin/mcp/settings",
                      json={"mode": "token", "open_scope": {"types": [], "swarms": bad}})
        assert r.status_code == 422, (bad, r.text)
    ok = admin.put("/api/admin/mcp/settings",
                   json={"mode": "token", "open_scope": {"types": [], "swarms": ["echo-base", None]}})
    assert ok.status_code == 200, ok.text


# --------------------------------------------------------------------------- #
# #447: open payloads
# --------------------------------------------------------------------------- #
def test_open_payloads_must_be_boolean(admin):
    for bad in ("yes", 1, None, [True]):
        r = admin.put("/api/admin/mcp/settings", json={
            "mode": "open", "open_scope": {"types": ["thought"], "payloads": bad}, "confirm": "open"})
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["error"]["code"] == "bad_scope"


def test_turning_on_open_payloads_needs_confirm_and_is_audited(admin):
    base = {"mode": "open", "open_scope": {"types": ["thought"], "swarms": None}}
    assert admin.put("/api/admin/mcp/settings", json={**base, "confirm": "open"}).status_code == 200
    on = {"mode": "open", "open_scope": {"types": ["thought"], "swarms": None, "payloads": True}}
    r = admin.put("/api/admin/mcp/settings", json=on)
    assert r.status_code == 422 and r.json()["error"]["code"] == "confirm_required"
    assert admin.put("/api/admin/mcp/settings", json={**on, "confirm": "open"}).status_code == 200
    assert admin.get("/api/admin/nav-status").json()["open_payloads"] is True
    # Turning it off again narrows access: no confirm needed.
    assert admin.put("/api/admin/mcp/settings", json=base).status_code == 200
    assert admin.get("/api/admin/nav-status").json()["open_payloads"] is False
    rows = admin.get("/api/admin/recall-log", params={"action": "mcp_settings_change"}).json()["entries"]
    by_new = [(e["detail"]["old"]["open_scope"].get("payloads"), e["detail"]["new"]["open_scope"].get("payloads"))
              for e in sorted(rows, key=lambda e: e["ts"])]
    assert (None, True) in by_new and (True, None) in by_new


def test_open_payloads_from_the_environment_are_validated():
    import pytest
    from server.config import load_config
    with pytest.raises(ValueError):
        load_config(mcp_open_scope={"types": ["thought"], "payloads": "yes"})
    assert load_config(mcp_open_scope={"types": ["thought"], "payloads": True}).mcp_open_scope["payloads"] is True
