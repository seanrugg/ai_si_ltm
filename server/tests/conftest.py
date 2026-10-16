"""Shared fixtures.

Tests run against SQLite in memory by default. Set ``LTMS_TEST_DATABASE_URL`` to
a PostgreSQL URL to run the same suite against Postgres (each test gets a fresh
schema); the deployment checklist requires one Postgres run before release.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from cli_agent.client import LTMSClient
from ltms_contract import generate_key
from server.config import load_config
from server.main import create_app

ADMIN_USER, ADMIN_PASS = "admin", "correct horse battery staple"
PG_URL = os.environ.get("LTMS_TEST_DATABASE_URL")


@pytest.fixture
def db_url():
    if not PG_URL:
        yield "sqlite+aiosqlite://"
        return
    # A fresh Postgres database per test (PG_URL should point at a maintenance DB).
    import asyncio

    from sqlalchemy.engine import make_url

    name = f"ltms_t_{uuid.uuid4().hex[:12]}"

    async def run(sql):
        eng = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
        async with eng.connect() as conn:
            await conn.execute(text(sql))
        await eng.dispose()

    asyncio.run(run(f'CREATE DATABASE "{name}"'))
    yield make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    asyncio.run(run(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
def config(db_url):
    return load_config(
        database_url=db_url,
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        cookie_secure=False,
        enable_scheduler=False,
        ingest_rate=1000,
        recall_rate=1000,
    )


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
def http(app):
    with TestClient(app, base_url="http://testserver") as client:
        yield client


class AdminClient:
    """Admin view over the shared TestClient (one event loop for the whole test,
    which asyncpg's connection pool requires). Keeps its own cookie."""

    def __init__(self, http: TestClient) -> None:
        self.http = http
        self.cookie: str | None = None
        self.headers = {"x-ltms-admin": "1"}

    def login(self, username=ADMIN_USER, password=ADMIN_PASS):
        r = self.http.post("/api/auth/admin/login", json={"username": username, "password": password})
        self.cookie = r.cookies.get("ltms_admin")
        self.http.cookies.clear()
        return r

    def request(self, method, url, **kw):
        headers = {**self.headers, **kw.pop("headers", {})}
        if self.cookie:
            headers["cookie"] = f"ltms_admin={self.cookie}"
        r = self.http.request(method, url, headers=headers, **kw)
        self.http.cookies.clear()
        return r

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)


@pytest.fixture
def admin(http):
    client = AdminClient(http)
    assert client.login().status_code == 200
    return client


class Mesh:
    """Helper to create enrolled members quickly."""

    def __init__(self, http: TestClient, admin: TestClient) -> None:
        self.http, self.admin = http, admin

    def member(self, name: str, *, kind: str = "robot", permissions=("contribute", "recall"),
               approve: bool = True, scope: dict | None = None, grant=None) -> LTMSClient:
        client = LTMSClient(name=name, signing_key=generate_key(), http=self.http)
        res = client.enroll(kind, list(permissions))
        if approve:
            body = {"scope": scope}
            if grant is not None:
                body["permissions"] = list(grant)
            r = self.admin.post(f"/api/admin/members/{res['id']}/approve", json=body)
            assert r.status_code == 200, r.text
        client.member_id = res["id"]
        return client


@pytest.fixture
def mesh(http, admin) -> Mesh:
    return Mesh(http, admin)


@pytest.fixture
def db_exec(app, http):
    """Run a SQL statement (or async callable taking a session) against the app's
    database from a sync test, through the TestClient's event loop."""

    def run(stmt):
        async def go():
            async with app.state.sessionmaker() as session:
                if callable(stmt):
                    result = await stmt(session)
                else:
                    result = await session.execute(stmt)
                await session.commit()
                return result

        return http.portal.call(go)

    return run
