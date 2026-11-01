"""Tests for ``server.migrate`` (issue #97).

Tests 1-3 need a PostgreSQL test database (``LTMS_TEST_DATABASE_URL``) and use
the ``db_url`` fixture from ``conftest.py``, which gives a fresh database per
test. Test 4 (a SQLite URL is refused) and test 5 (``split_sql`` unit test) run
everywhere.
"""
from __future__ import annotations

import asyncio
import io
import os
import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# ``split_sql`` lives in the baseline migration module, whose name leads with a
# digit, so import it through importlib.
import importlib

_baseline = importlib.import_module("server.migrations.versions.0001_baseline")
split_sql = _baseline.split_sql

PG_URL = os.environ.get("LTMS_TEST_DATABASE_URL")

needs_postgres = pytest.mark.skipif(
    not PG_URL,
    reason="migration tests need a PostgreSQL test database (LTMS_TEST_DATABASE_URL)",
)

SCHEMA_SQL = pathlib.Path(__file__).resolve().parent.parent.parent / "config" / "schema.sql"


def _invoke(argv, db_url):
    """Call ``server.migrate.main`` in-process; return (exit_code, stdout, stderr)."""
    from contextlib import redirect_stderr, redirect_stdout

    from server.migrate import main

    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            main([*argv, "--url", db_url])
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    return code, out.getvalue(), err.getvalue()


async def _apply_schema(db_url, sql_text):
    """Apply a SQL file (split into statements) to a fresh database."""
    engine = create_async_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for stmt in split_sql(sql_text):
                await conn.exec_driver_sql(stmt)
    finally:
        await engine.dispose()


async def _trigger_names(db_url):
    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:

            def go(connection):
                rows = connection.execute(
                    text(
                        "SELECT t.tgname FROM pg_trigger t "
                        "JOIN pg_class c ON c.oid = t.tgrelid "
                        "WHERE c.relname = 'records'"
                    )
                ).all()
                return [row[0] for row in rows]

            return await conn.run_sync(go)
    finally:
        await engine.dispose()


@needs_postgres
def test_upgrade_empty_db(db_url):
    code, _, err = _invoke(["upgrade"], db_url)
    assert code == 0, err

    code, out, err = _invoke(["current"], db_url)
    assert code == 0
    assert out.strip() == "0001", err

    triggers = asyncio.run(_trigger_names(db_url))
    assert "trg_records_no_mutation" in triggers
    assert "trg_records_no_truncate" in triggers


@needs_postgres
def test_check(db_url):
    # a fresh empty database is behind head
    code, out, _ = _invoke(["check"], db_url)
    assert code == 1
    assert out.strip() == "behind: none -> 0001"

    _invoke(["upgrade"], db_url)

    code, out, _ = _invoke(["check"], db_url)
    assert code == 0
    assert out.strip() == "ok 0001"


@needs_postgres
def test_pre_migration_db(db_url):
    schema_sql = SCHEMA_SQL.read_text()
    asyncio.run(_apply_schema(db_url, schema_sql))

    # upgrade refuses: the database predates migrations
    code, _, err = _invoke(["upgrade"], db_url)
    assert code == 1
    assert "predates migrations" in err
    assert "python -m server.migrate stamp 0001" in err

    # stamping the baseline satisfies the check
    code, _, err = _invoke(["stamp", "0001"], db_url)
    assert code == 0, err

    code, out, _ = _invoke(["check"], db_url)
    assert code == 0
    assert out.strip() == "ok 0001"


def test_sqlite_url_refused():
    code, _, err = _invoke(["upgrade"], "sqlite+aiosqlite://")
    assert code == 2
    assert "PostgreSQL" in err


def test_split_sql_dollar_quoted_function():
    sql = """\
CREATE TABLE t (id INTEGER);

CREATE OR REPLACE FUNCTION f() RETURNS trigger AS $$
BEGIN
  INSERT INTO t VALUES (1);  -- semicolon inside the body
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TABLE u (id INTEGER);
"""
    assert split_sql(sql) == [
        "CREATE TABLE t (id INTEGER);",
        "CREATE OR REPLACE FUNCTION f() RETURNS trigger AS $$\n"
        "BEGIN\n"
        "  INSERT INTO t VALUES (1);  -- semicolon inside the body\n"
        "  RETURN NEW;\n"
        "END\n"
        "$$ LANGUAGE plpgsql;",
        "CREATE TABLE u (id INTEGER);",
    ]
