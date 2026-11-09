"""Drop and recreate a disposable PostgreSQL test database (issue #143).

    LTMS_DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>/<name>_test \\
        python -m scripts.reset_test_db --confirm <name>_test

Only for scale and volume testing. Refuses any database whose name doesn't end
in ``_test``, any driver other than PostgreSQL, and any ``--confirm`` that
doesn't repeat the name exactly. Never prints the URL (it holds the password).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

NAME_RE = re.compile(r"^[a-z0-9_]+_test$")


def check_target(url: str, confirm: str) -> str:
    """Return the database name if it is safe to drop, else raise ValueError."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("only PostgreSQL test databases can be reset")
    name = parsed.database or ""
    if not NAME_RE.match(name):
        raise ValueError("the database name must match ^[a-z0-9_]+_test$")
    if confirm != name:
        raise ValueError("--confirm must repeat the database name exactly")
    return name


async def reset(url: str, name: str) -> None:
    maintenance = make_url(url).set(database="postgres")
    engine = create_async_engine(maintenance, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            # name is checked against NAME_RE, so quoting it is safe.
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drop and recreate a disposable *_test PostgreSQL database.")
    parser.add_argument("--confirm", required=True, help="the database name, repeated exactly")
    args = parser.parse_args(argv)
    url = os.environ.get("LTMS_DATABASE_URL", "")
    if not url:
        print("LTMS_DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        name = check_target(url, args.confirm)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    asyncio.run(reset(url, name))
    print(f"reset {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
