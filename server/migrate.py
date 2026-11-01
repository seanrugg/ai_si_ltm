"""LTMS migration CLI (issue #97).

Usage::

    python -m server.migrate {current,upgrade,check,stamp} [--url URL]

``--url`` defaults to ``ServerConfig().resolved_database_url``. Only
PostgreSQL URLs are accepted; SQLite keeps ``create_all``.

Alembic commands are called as plain sync code (each invocation is its own
process, so there is no running event loop for ``env.py``'s ``asyncio.run``).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from server.config import ServerConfig


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "server:migrations")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _require_postgres(url: str) -> None:
    if not (url.startswith("postgresql") or url.startswith("postgres+")):
        print(
            "migrations require a PostgreSQL URL; SQLite keeps create_all",
            file=sys.stderr,
        )
        sys.exit(2)


async def _current_revision(url: str):
    """Return the database's current Alembic revision, or ``None``."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:

            def resolve(connection):
                return MigrationContext.configure(connection).get_current_revision()

            return await conn.run_sync(resolve)
    finally:
        await engine.dispose()


async def _table_exists(url: str, table: str) -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:

            def check(connection):
                row = connection.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = :t"
                    ),
                    {"t": table},
                ).first()
                return row is not None

            return await conn.run_sync(check)
    finally:
        await engine.dispose()


def _head(cfg: Config) -> str:
    return ScriptDirectory.from_config(cfg).get_current_head()


def _cmd_current(url: str) -> None:
    rev = asyncio.run(_current_revision(url))
    print(rev if rev is not None else "none")


def _cmd_check(url: str) -> None:
    cfg = alembic_config(url)
    head = _head(cfg)
    rev = asyncio.run(_current_revision(url))
    rev_text = rev if rev is not None else "none"
    if rev == head:
        print(f"ok {rev_text}")
        sys.exit(0)
    else:
        print(f"behind: {rev_text} -> {head}")
        sys.exit(1)


def _cmd_upgrade(url: str) -> None:
    # A database that already holds records but was never stamped predates
    # migrations; it must be stamped to the baseline before upgrading.
    records = asyncio.run(_table_exists(url, "records"))
    versioned = asyncio.run(_table_exists(url, "alembic_version"))
    if records and not versioned:
        print(
            "This database predates migrations. If its schema matches "
            "config/schema.sql at revision 0001, run: "
            "python -m server.migrate stamp 0001",
            file=sys.stderr,
        )
        sys.exit(1)
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")


def _cmd_stamp(url: str, rev: str) -> None:
    cfg = alembic_config(url)
    command.stamp(cfg, rev)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="server.migrate")
    parser.add_argument(
        "command", choices=["current", "upgrade", "check", "stamp"]
    )
    parser.add_argument("revision", nargs="?", help="revision for `stamp`")
    parser.add_argument(
        "--url", default=None, help="database URL (defaults to LTMS config)"
    )
    args = parser.parse_args(argv)

    url = args.url or ServerConfig().resolved_database_url
    _require_postgres(url)

    if args.command == "current":
        _cmd_current(url)
    elif args.command == "upgrade":
        _cmd_upgrade(url)
    elif args.command == "check":
        _cmd_check(url)
    elif args.command == "stamp":
        if not args.revision:
            parser.error("stamp requires a revision argument")
        _cmd_stamp(url, args.revision)


if __name__ == "__main__":
    main()
