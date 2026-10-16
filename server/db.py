"""Async engine + session factory. The app keeps its own engine on ``app.state``
so tests can run any number of isolated apps side by side."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from server.models import Base


def make_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url.endswith("sqlite+aiosqlite://"):
            kwargs["poolclass"] = StaticPool
        engine = create_async_engine(url, **kwargs)

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create any missing tables. Idempotent for tables; on Postgres prefer
    running ``config/schema.sql`` (see PLESK_DEPLOYMENT_PLAN.md)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
