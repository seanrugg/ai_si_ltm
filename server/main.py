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
from server.api import datasets as datasets_api
from server.api.errors import register_exception_handlers
from server.config import ServerConfig, load_config
from server.db import create_all, make_engine, make_sessionmaker
from server.migrate import alembic_config, _current_revision, _head
from server.mcp_remote import _mcp_allowed_hosts, create_mcp_server
from server.security.ratelimit import RateLimiter
from server.services import lrs
from server.services.auth import ensure_admin

from mcp.server.streamable_http import TransportSecuritySettings

# The dashboard handles swarm root keys in the browser (issue #122), so it gets
# a strict CSP: only its own bundled scripts run. Vite inlines small font files
# as data: URIs, hence font-src. React style props go through the CSSOM, which
# style-src doesn't restrict, so no 'unsafe-inline' is needed.
DASHBOARD_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self' data:; "
                 "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                 "form-action 'self'; frame-ancestors 'none'")


class _McpNoSlash:
    """Serve ``/mcp`` as ``/mcp/`` without a redirect.

    The MCP app is mounted at ``/mcp``, which only matches ``/mcp/...``. A bare
    ``/mcp`` (what people type into an MCP client) would otherwise fall through
    to the dashboard's catch-all and get 405, and a redirect would make some
    clients drop the POST body. So rewrite the path before routing.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
        await self.app(scope, receive, send)


def create_app(config: ServerConfig | None = None, *, create_tables: bool = True) -> FastAPI:
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(cfg.resolved_database_url)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        if create_tables:
            url = cfg.resolved_database_url
            if url.startswith("sqlite") or cfg.schema_mode == "create":
                await create_all(engine)
            else:
                # PostgreSQL in "migrate" mode: the database must already be at
                # the Alembic head. Never create tables here; that would silently
                # drift the schema away from the signed record contract.
                current = await _current_revision(url)
                head = _head(alembic_config(url))
                if current != head:
                    raise RuntimeError(
                        f"database schema is at {current}, this LTMS needs {head}: "
                        "run python -m server.migrate upgrade"
                    )
        async with app.state.sessionmaker() as session:
            await ensure_admin(session, cfg.admin_username, cfg.admin_password)
            await lrs.bootstrap_endpoint(session, cfg)
            await session.commit()
        scheduler = None
        if cfg.enable_scheduler:
            from server.tasks import start_scheduler
            scheduler = start_scheduler(app)
        try:
            async with mcp_server.session_manager.run():
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
        path = request.url.path
        if not (path.startswith("/api") or path == "/mcp" or path.startswith("/mcp/")):
            response.headers["content-security-policy"] = DASHBOARD_CSP
        return response

    @app.get("/api/health", include_in_schema=False)
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    app.include_router(public.router)
    app.include_router(member.router)
    app.include_router(datasets_api.router)
    app.include_router(admin.router)
    register_exception_handlers(app)

    # Remote MCP read-only endpoint (issue #107), mounted before the dashboard's
    # StaticFiles catch-all so /mcp takes precedence.
    mcp_server = create_mcp_server(cfg, lambda: app.state.sessionmaker())
    app.mount(
        "/mcp",
        mcp_server.streamable_http_app(
            streamable_http_path="/",
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                allowed_hosts=_mcp_allowed_hosts(cfg),
            ),
        ),
        name="mcp",
    )
    app.add_middleware(_McpNoSlash)

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
