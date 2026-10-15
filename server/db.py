"""Async SQLAlchemy engine, session factory, and FastAPI dependency.

The production URL points at PostgreSQL (filled in from env by ``config.py``);
tests override it with an in-memory SQLite URL so the whole chain can be
exercised without a running Postgres server.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import DeclarativeBase

from server.config import get_config


def _sqlite_fk_on_connect(dbapi_connection, connection_record):
    """Enable foreign-key enforcement per SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(database_url: str | None = None) -> AsyncEngine:
    """Build a :class:`AsyncEngine` for ``database_url`` (defaults to config)."""
    database_url = database_url or get_config().database_url
    connect_args: dict[str, object] = {}
    use_sqlite = database_url.startswith("sqlite")
    if use_sqlite:
        connect_args["check_same_thread"] = False
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )
    if use_sqlite:
        event.listens_for(engine.sync_engine, "connect")(_sqlite_fk_on_connect)
    return engine


config = get_config()
engine: AsyncEngine = make_engine(config.database_url)


def async_session_factory() -> AsyncSession:
    """Open an :class:`AsyncSession` bound to the current module engine.

    Reads ``engine`` dynamically so callers (``deps.py`` / ``routes.py`` capture
    this name by value) always use whatever engine is active — the fixture only
    needs to rebind ``db_module.engine``.
    """
    session: sessionmaker = sessionmaker(
        globals()["engine"],
        expire_on_commit=False,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
    )
    return session()


async def create_all() -> None:
    """Create every table registered on the declarative metadata.

    Reads ``engine`` from the module namespace so callers that rebind
    ``db_module.engine`` (e.g. tests swapping in a SQLite URL) hit the right
    engine.
    """
    from server.models.base import Base

    engine = globals()["engine"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> Iterator[AsyncSession]:
    """FastAPI dependency: yield a session, commit, then close."""
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def session_scope() -> Iterator[AsyncSession]:
    """Context manager yielding a session for service-layer transactions."""
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def invalidate_connection_pool() -> None:
    """Dispose the engine's connection pool (useful after schema changes)."""
    engine.dispose()
