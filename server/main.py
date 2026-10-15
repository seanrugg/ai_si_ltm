"""Application factory: build the FastAPI app, mount routers, lifecycle hooks.

``create_app()`` assembles the routers and registers the exception handlers. On
startup it creates all tables (idempotent — safe on every boot) and, on the
very first start with a configured ``admin_password``, seeds an admin user so
the dashboard is usable out of the box.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.config import get_config
from server.db import create_all, session_scope
from server.models.adminuser import AdminUser
from server.api.errors import register_exception_handlers
from server.api.routes import admin_router, member_router, public_router
from server.security.auth import hash_password
from server.services.retention import setup_scheduler

config = get_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables + seed admin; shutdown: stop the scheduler."""
    await create_all()
    await _seed_admin()
    setup_scheduler(app)
    yield
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        await scheduler.shutdown()


async def _seed_admin() -> None:
    """Create the admin user on first start when ``admin_password`` is set."""
    password = config.admin_password
    if not password:
        return
    async with session_scope() as session:
        stmt = AdminUser.select().where(AdminUser.username == config.admin_username)
        existing = await session.scalar(stmt)
        if existing is not None:
            return
        session.add(
            AdminUser(
                username=config.admin_username,
                password_hash=hash_password(password),
            )
        )


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="LTMS — Long-Term Memory Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(public_router)
    app.include_router(member_router)
    app.include_router(admin_router)
    register_exception_handlers(app)
    return app


app = create_app()
