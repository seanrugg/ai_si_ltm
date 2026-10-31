"""Application factory.

Run locally::

    LTMS_DATABASE_URL=sqlite+aiosqlite:///./dev.db LTMS_ADMIN_PASSWORD=change-me \
    LTMS_COOKIE_SECURE=false uvicorn server.main:app --port 8080

Production runs one worker behind nginx (see PLESK_DEPLOYMENT_PLAN.md).
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from server import __version__
from server.api import admin, member, public
from server.api.errors import register_exception_handlers
from server.config import ServerConfig, load_config
from server.db import create_all, make_engine, make_sessionmaker
from server.security.ratelimit import RateLimiter
from server.services import lrs
from server.services.auth import ensure_admin


def create_app(config: ServerConfig | None = None, *, create_tables: bool = True) -> FastAPI:
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(cfg.resolved_database_url)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        if create_tables:
            await create_all(engine)
        async with app.state.sessionmaker() as session:
            await ensure_admin(session, cfg.admin_username, cfg.admin_password)
            await lrs.bootstrap_endpoint(session, cfg)
            await session.commit()
        scheduler = None
        if cfg.enable_scheduler:
            from server.tasks import start_scheduler
            scheduler = start_scheduler(app)
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            http = getattr(app.state, "lrs_forward_http", None)
            if http is not None:
                await http.aclose()
            await engine.dispose()

    app = FastAPI(title="LTMS — Long-Term Memory Server", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)  # no public pages
    app.state.config = cfg
    app.state.limiters = {
        "ingest": RateLimiter(cfg.ingest_rate),
        "recall": RateLimiter(cfg.recall_rate),
        "enroll": RateLimiter(cfg.enroll_rate_per_ip, per=3600),
        "challenge": RateLimiter(30, per=60),
        "admin_login": RateLimiter(10, per=60),
    }

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or secrets.token_hex(8)
        request.state.request_id = rid[:64]
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        if request.url.path.startswith("/api"):
            response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        return response

    @app.get("/api/health", include_in_schema=False)
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    app.include_router(public.router)
    app.include_router(member.router)
    app.include_router(admin.router)
    register_exception_handlers(app)

    if cfg.dashboard_dir:
        from pathlib import Path

        from fastapi.staticfiles import StaticFiles

        dist = Path(cfg.dashboard_dir)
        if not (dist / "index.html").exists():
            raise RuntimeError(f"LTMS_DASHBOARD_DIR={dist} has no index.html; run `npm run build` in dashboard/")
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    return app


def __getattr__(name: str):
    """``uvicorn server.main:app`` builds the app on first access (PEP 562), so
    importing this module in tests does not read the environment."""
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
