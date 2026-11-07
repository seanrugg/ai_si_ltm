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
import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
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
    assert out.strip() == "0002", err

    triggers = asyncio.run(_trigger_names(db_url))
    assert "trg_records_no_mutation" in triggers
    assert "trg_records_no_truncate" in triggers


@needs_postgres
def test_check(db_url):
    # a fresh empty database is behind head
    code, out, _ = _invoke(["check"], db_url)
    assert code == 1
    assert out.strip() == "behind: none -> 0002"

    _invoke(["upgrade"], db_url)

    code, out, _ = _invoke(["check"], db_url)
    assert code == 0
    assert out.strip() == "ok 0002"


@needs_postgres
def test_pre_migration_db(db_url):
    schema_sql = SCHEMA_SQL.read_text()
    asyncio.run(_apply_schema(db_url, schema_sql))

    # upgrade refuses: the database predates migrations
    code, _, err = _invoke(["upgrade"], db_url)
    assert code == 1
    assert "predates migrations" in err
    assert "python -m server.migrate stamp 0001" in err

    # stamping the baseline brings it to 0001, which is now behind head 0002
    code, _, err = _invoke(["stamp", "0001"], db_url)
    assert code == 0, err

    code, out, _ = _invoke(["check"], db_url)
    assert code == 1
    assert out.strip() == "behind: 0001 -> 0002", out

    # stamping to head satisfies the check
    code, _, err = _invoke(["stamp", "0002"], db_url)
    assert code == 0, err

    code, out, _ = _invoke(["check"], db_url)
    assert code == 0
    assert out.strip() == "ok 0002"


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



def test_baseline_has_no_transaction_wrappers():
    # Runs everywhere: the baseline runs inside Alembic's own transaction, so the
    # file's BEGIN;/COMMIT; must be skipped (a COMMIT would split the migration).
    import importlib.util, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0001_baseline.py"
    spec = importlib.util.spec_from_file_location("baseline_0001", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stmts = mod.baseline_statements()
    assert stmts, "baseline is empty"
    assert all(s.strip().upper() not in ("BEGIN;", "COMMIT;") for s in stmts)
    # the function body containing BEGIN ... END stays one statement
    assert any("records_no_mutation" in s and "$$" in s for s in stmts)


def _db_password(db_url: str) -> str | None:
    return make_url(db_url).password


def _stage_db_at_0001_with_record(db_url):
    """Apply the baseline schema, stamp revision 0001, and insert one record row.

    Plain function: each async step gets its own asyncio.run, and the migrate CLI
    (which runs Alembic's asyncio env.py) is called with no loop running.
    """
    asyncio.run(_apply_schema(db_url, SCHEMA_SQL.read_text()))
    _invoke(["stamp", "0001"], db_url)
    asyncio.run(_insert_one_record(db_url))


async def _insert_one_record(db_url):
    engine = create_async_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql(
                "INSERT INTO members (id, public_key, name, kind, permissions, "
                "requested_permissions, status, last_ack_seq, last_hash, created_at) "
                "VALUES ('00000000-0000-0000-0000-000000000001', '\\x00'::bytea, "
                "'test-member', 'robot', '[]'::jsonb, '[]'::jsonb, 'active', 0, "
                "'genesis', NOW())"
            )
            await conn.exec_driver_sql(
                "INSERT INTO records (member_id, agent, seq, ts, type, prev_hash, "
                "payload_hash, hash, sig, header_raw, received_at) "
                "VALUES ('00000000-0000-0000-0000-000000000001', 'test', 1, NOW(), "
                "'event', 'genesis', 'aaaa', 'aaaa', 'sig', '\\x00'::bytea, NOW())"
            )
    finally:
        await engine.dispose()


@needs_postgres
def test_upgrade_backs_up_before_migrating(db_url, monkeypatch, tmp_path):
    # A database at revision 0001 with a record, then a (faked) pending head.
    _stage_db_at_0001_with_record(db_url)

    upgraded = []
    monkeypatch.setattr("server.migrate._head", lambda cfg: "0002")
    monkeypatch.setattr(
        "alembic.command.upgrade", lambda cfg, rev: upgraded.append(rev)
    )
    monkeypatch.setattr("shutil.which", lambda cmd: "/fake/pg_dump" if cmd == "pg_dump" else None)

    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = list(cmd)
        captured["env"] = kw.get("env")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("subprocess.run", fake_run)

    code, out, err = _invoke(["upgrade", "--backup-dir", str(tmp_path)], db_url)
    assert code == 0, err
    assert upgraded == ["head"], upgraded

    cmd = captured["cmd"]
    assert "--format=custom" in cmd, cmd
    file_idx = cmd.index("--file")
    backup_file = cmd[file_idx + 1]
    assert backup_file.startswith(str(tmp_path) + os.sep), backup_file
    assert os.path.basename(backup_file).startswith("ltms-pre-0001-to-0002-"), backup_file
    assert backup_file.endswith(".dump"), backup_file

    env = captured["env"]
    assert env is not None and "PGPASSWORD" in env, env
    password = _db_password(db_url)
    assert password is not None
    # the password is never on the command line and never printed
    # no password in the dump URL (the test user's password equals its name,
    # so check the URL's password field rather than searching for the text)
    from urllib.parse import urlparse
    assert urlparse(cmd[-1]).password is None, cmd
    assert password not in out and password not in err


@needs_postgres
def test_upgrade_refuses_when_backup_dir_unusable(db_url, monkeypatch, tmp_path):
    # A backup dir that can't be created: a clean message and no migration,
    # not a traceback (e.g. the default /var/backups/ltms without permission).
    _stage_db_at_0001_with_record(db_url)
    upgraded = []
    monkeypatch.setattr("server.migrate._head", lambda cfg: "0002")
    monkeypatch.setattr("alembic.command.upgrade", lambda cfg, rev: upgraded.append(rev))
    monkeypatch.setattr("shutil.which", lambda cmd: "/fake/pg_dump")
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    code, _, err = _invoke(["upgrade", "--backup-dir", str(blocker / "sub")], db_url)
    assert code == 1, err
    assert "backup failed" in err and "not migrating" in err, err
    assert upgraded == []


@needs_postgres
def test_upgrade_refuses_without_pg_dump(db_url, monkeypatch):
    _stage_db_at_0001_with_record(db_url)

    upgraded = []
    monkeypatch.setattr("server.migrate._head", lambda cfg: "0002")
    monkeypatch.setattr(
        "alembic.command.upgrade", lambda cfg, rev: upgraded.append(rev)
    )
    monkeypatch.setattr("shutil.which", lambda cmd: None)  # pg_dump missing

    code, _, err = _invoke(["upgrade"], db_url)
    assert code == 1, err
    assert "backup failed" in err and "not migrating" in err, err
    assert upgraded == [], upgraded  # did not migrate

    code, out, _ = _invoke(["current"], db_url)
    assert code == 0
    assert out.strip() == "0001", out


@needs_postgres
def test_upgrade_no_backup_warns_and_migrates(db_url, monkeypatch):
    _stage_db_at_0001_with_record(db_url)

    upgraded = []
    monkeypatch.setattr("server.migrate._head", lambda cfg: "0002")
    monkeypatch.setattr(
        "alembic.command.upgrade", lambda cfg, rev: upgraded.append(rev)
    )
    monkeypatch.setattr("shutil.which", lambda cmd: "/fake/pg_dump")
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0)
    )

    code, out, err = _invoke(["upgrade", "--no-backup"], db_url)
    assert code == 0, err
    assert "warning" in (out + err).lower(), (out, err)
    assert upgraded == ["head"], upgraded


@needs_postgres
def test_lifespan_refuses_wrong_schema_and_passes_after_upgrade(db_url):
    from fastapi.testclient import TestClient

    from server.config import load_config
    from server.main import create_app

    cfg = load_config(
        database_url=db_url,
        schema_mode="migrate",
        admin_username="admin",
        admin_password="correct horse battery staple",
        cookie_secure=False,
        enable_scheduler=False,
    )

    # Empty database: schema is at None, this LTMS needs 0001 -> startup refuses.
    with pytest.raises(RuntimeError, match="server.migrate upgrade"):
        with TestClient(create_app(cfg)):
            pass

    # After upgrading to head, startup succeeds (no create_all in migrate mode).
    assert _invoke(["upgrade"], db_url)[0] == 0
    with TestClient(create_app(cfg)) as client:
        r = client.get("/api/health")
        assert r.status_code == 200, r.text


def test_sqlite_starts_in_migrate_mode():
    # Runs everywhere: SQLite always uses create_all regardless of schema_mode.
    from fastapi.testclient import TestClient

    from server.config import load_config
    from server.main import create_app

    cfg = load_config(
        database_url="sqlite+aiosqlite://",
        schema_mode="migrate",
        admin_username="admin",
        admin_password="correct horse battery staple",
        cookie_secure=False,
        enable_scheduler=False,
    )
    with TestClient(create_app(cfg)) as client:
        r = client.get("/api/health")
        assert r.status_code == 200, r.text


def _seed_one_record(db_url):
    """Insert one member and one signed record (tables are assumed to exist)."""
    import asyncio

    async def go():
        eng = create_async_engine(db_url, isolation_level="AUTOCOMMIT")
        try:
            async with eng.connect() as conn:
                await conn.exec_driver_sql(
                    "INSERT INTO members (id, public_key, name, kind, permissions, "
                    "requested_permissions, status, last_ack_seq, last_hash, created_at) "
                    "VALUES ('00000000-0000-0000-0000-000000000001', '\\x00'::bytea, "
                    "'alice', 'robot', '[]'::jsonb, '[]'::jsonb, 'active', 0, "
                    "'genesis', NOW())"
                )
                await conn.exec_driver_sql(
                    "INSERT INTO records (member_id, agent, seq, ts, type, prev_hash, "
                    "payload_hash, hash, sig, header_raw, received_at) "
                    "VALUES ('00000000-0000-0000-0000-000000000001', 'test', 1, NOW(), "
                    "'event', 'genesis', 'aaaa', 'aaaa', 'sig', '\\x00'::bytea, NOW())"
                )
        finally:
            await eng.dispose()

    asyncio.run(go())


@needs_postgres
def test_no_model_drift_after_upgrade(db_url):
    """Upgrade an empty database to head; the model metadata must match exactly
    (no drift). A model change without a migration fails this test."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from server.models import Base

    assert _invoke(["upgrade"], db_url)[0] == 0

    async def go():
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:

                def compare(connection):
                    context = MigrationContext.configure(connection)
                    return compare_metadata(context, Base.metadata)

                return await conn.run_sync(compare)
        finally:
            await engine.dispose()

    diff = asyncio.run(go())
    assert diff == [], diff


@needs_postgres
def test_signed_records_survive_upgrade(db_url):
    """Create a database at revision 0001 (== head today), start the app on the
    existing schema with create_tables=False, append signed records, upgrade to
    head: the chain still verifies."""
    from fastapi.testclient import TestClient

    from cli_agent.client import LTMSClient
    from ltms_contract import generate_key

    from server.config import load_config
    from server.main import create_app

    # 1. Create the database at revision 0001 (schema exists, stamped).
    assert _invoke(["upgrade"], db_url)[0] == 0

    # 2. Start the app on the existing schema; create_tables=False so it never
    #    creates tables itself. The DB is at head, so "migrate" mode starts
    #    without creating anything.
    from server.tests.conftest import ADMIN_PASS, ADMIN_USER, AdminClient

    cfg = load_config(
        database_url=db_url,
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        cookie_secure=False,
        enable_scheduler=False,
    )
    app = create_app(cfg, create_tables=False)

    with TestClient(app, base_url="http://testserver") as http:
        admin = AdminClient(http)
        assert admin.login().status_code == 200
        # 3. Enroll + approve a member and append 3 signed records (mirrors mesh).
        member = LTMSClient(name="alice", signing_key=generate_key(), http=http)
        res = member.enroll("robot", ["contribute", "recall"])
        assert admin.post(f"/api/admin/members/{res['id']}/approve", json={}).status_code == 200
        for i in range(1, 4):
            member.append("event", summary=f"record {i}", payload={"n": i})
        member.flush()

        # 4. Upgrade to head (0001 -> head; no-op today).
        assert _invoke(["upgrade"], db_url)[0] == 0

        # 5. Integrity: the signed chain still verifies after the upgrade.
        # A fresh engine: the app's engine belongs to the TestClient's event loop.
        async def go():
            from server.db import make_engine, make_sessionmaker
            from server.services.integrity import verify_all

            engine = make_engine(db_url)
            try:
                async with make_sessionmaker(engine)() as session:
                    return await verify_all(session)
            finally:
                await engine.dispose()

        reports = asyncio.run(go())
        assert len(reports) == 1
        assert reports[0]["status"] == "ok", reports


@needs_postgres
def test_records_stay_append_only_after_upgrade(db_url):
    """After upgrading to head, UPDATE on records is still rejected by the
    append-only trigger."""
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from server.config import load_config
    from server.main import create_app

    assert _invoke(["upgrade"], db_url)[0] == 0

    cfg = load_config(
        database_url=db_url,
        admin_username="admin",
        admin_password="correct horse battery staple",
        cookie_secure=False,
        enable_scheduler=False,
    )
    app = create_app(cfg, create_tables=False)

    with TestClient(app, base_url="http://testserver"):
        _seed_one_record(db_url)

        async def go():
            async with app.state.sessionmaker() as session:
                await session.execute(text("UPDATE records SET seq = seq"))
                await session.commit()

        with pytest.raises(Exception):
            asyncio.run(go())
