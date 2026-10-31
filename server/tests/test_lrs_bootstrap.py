"""LRS bootstrap: register one endpoint from env on first start (issue #86)."""
from __future__ import annotations

from sqlalchemy import select

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from server.config import load_config
from server.main import create_app
from server.models import AuditLog, LrsEndpoint
from server.services import lrs as lrs_service
from server.tests.conftest import ADMIN_PASS, ADMIN_USER


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
        secret_key=Fernet.generate_key().decode(),
    )


def _bootstrap_cfg(config, **overrides):
    """Config with LRS bootstrap opts filled in, overridable per test."""
    base = dict(
        lrs_bootstrap_url="https://bootstrap.example.org/",
        lrs_bootstrap_name="bootstrap-lrs",
        lrs_bootstrap_username="bob",
        lrs_bootstrap_secret="bootstrap-secret",
        lrs_bootstrap_xapi_version="2.0.0",
        lrs_bootstrap_types="event,action",
        lrs_bootstrap_enabled=True,
    )
    base.update(overrides)
    return config.model_copy(update=base)


def test_url_unset_creates_no_endpoint(config):
    # lrs_bootstrap_url defaults to "" -> nothing created, LTMS still starts.
    app = create_app(config)
    with TestClient(app) as http:

        async def check():
            async with app.state.sessionmaker() as session:
                rows = list((await session.execute(select(LrsEndpoint))).scalars())
                assert rows == []

        http.portal.call(check)


def test_bootstrap_creates_endpoint(config):
    cfg = _bootstrap_cfg(config)
    app = create_app(cfg)
    with TestClient(app) as http:

        async def check():
            async with app.state.sessionmaker() as session:
                rows = list((await session.execute(select(LrsEndpoint))).scalars())
                assert len(rows) == 1
                ep = rows[0]
                assert ep.name == "bootstrap-lrs"
                assert ep.url == "https://bootstrap.example.org"  # trailing slash stripped
                assert ep.username == "bob"
                assert ep.secret_enc is not None  # has_secret
                assert ep.enabled is True
                assert ep.scope == {"types": ["event", "action"]}
                audits = list(
                    (await session.execute(
                        select(AuditLog).where(AuditLog.action == "lrs_create")
                    )).scalars()
                )
                assert len(audits) == 1
                assert audits[0].actor == "bootstrap"
                assert "bootstrap-secret" not in str(audits[0].detail)
                assert "secret_enc" not in str(audits[0].detail)

        http.portal.call(check)


def test_bootstrap_runs_once_only(config):
    # Simulated restart: call bootstrap again with an endpoint already present.
    cfg = _bootstrap_cfg(config)
    app = create_app(cfg)
    with TestClient(app) as http:

        async def run_again():
            async with app.state.sessionmaker() as session:
                result = await lrs_service.bootstrap_endpoint(session, cfg)
                await session.commit()
                rows = list((await session.execute(select(LrsEndpoint))).scalars())
                return result, len(rows), rows[0].enabled if rows else None

        result, count, enabled = http.portal.call(run_again)
        assert result is None
        assert count == 1
        assert enabled is True


def test_bootstrap_skips_when_admin_endpoint_exists(config, http, admin):
    # An endpoint created through the admin API exists when the app (re)starts;
    # bootstrap must do nothing (one-time only).
    r = admin.post("/api/admin/lrs", json={
        "name": "admin-lrs",
        "url": "https://admin-lrs.example.org/",
        "username": "alice",
        "secret": "s3cret",
        "xapi_version": "2.0.0",
        "scope": {"types": ["communication"]},
    })
    assert r.status_code == 200, r.text
    cfg = _bootstrap_cfg(config)

    async def run():
        async with http.app.state.sessionmaker() as session:
            result = await lrs_service.bootstrap_endpoint(session, cfg)
            await session.commit()
            rows = list((await session.execute(select(LrsEndpoint))).scalars())
            return result, len(rows)

    result, count = http.portal.call(run)
    assert result is None
    assert count == 1

def test_secret_requires_secret_key(config, caplog):
    # Secret set but LTMS_SECRET_KEY unset -> create_endpoint raises, bootstrap
    # logs one error line (code + message, never the secret) and returns None.
    cfg = config.model_copy(update={
        "lrs_bootstrap_url": "https://bootstrap.example.org/",
        "lrs_bootstrap_secret": "bootstrap-secret",
        "lrs_bootstrap_enabled": True,
        "secret_key": None,
    })
    app = create_app(cfg)
    with TestClient(app) as http:

        async def check():
            async with app.state.sessionmaker() as session:
                rows = list((await session.execute(select(LrsEndpoint))).scalars())
                assert rows == []

        http.portal.call(check)

    assert "no_secret_key" in caplog.text
    assert "bootstrap-secret" not in caplog.text


def test_types_empty_creates_endpoint_with_empty_scope(config):
    cfg = _bootstrap_cfg(config, lrs_bootstrap_types="", lrs_bootstrap_enabled=True)
    app = create_app(cfg)
    with TestClient(app) as http:

        async def check():
            async with app.state.sessionmaker() as session:
                rows = list((await session.execute(select(LrsEndpoint))).scalars())
                assert len(rows) == 1
                assert rows[0].scope == {"types": []}

        http.portal.call(check)
