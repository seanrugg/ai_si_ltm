"""LRS endpoints: storage, encrypted credentials, admin API (issue #59)."""
from __future__ import annotations

from sqlalchemy import select

import httpx
import pytest
from cryptography.fernet import Fernet

from server.config import load_config
from server.models import AuditLog, LrsEndpoint
from server.services import lrs as lrs_service
from server.services.errors import ServiceError
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


def _body(name="lrs-one", url="https://lrs.example.org/", secret="s3cret", **kw):
    b = {
        "name": name,
        "url": url,
        "username": "alice",
        "secret": secret,
        "xapi_version": "2.0.0",
        "scope": {"types": ["communication"]},
    }
    b.update(kw)
    return b


def test_create_then_list_hides_secret(admin):
    r = admin.post("/api/admin/lrs", json=_body())
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["has_secret"] is True
    assert "secret_enc" not in created
    assert created["url"] == "https://lrs.example.org"  # trailing slash stripped
    assert created["xapi_version"] == "2.0.0" and created["enabled"] is False

    listing = admin.get("/api/admin/lrs").json()["endpoints"]
    assert len(listing) == 1
    assert listing[0]["has_secret"] is True
    assert "secret_enc" not in listing[0]


def test_create_without_secret(admin):
    r = admin.post("/api/admin/lrs", json=_body(secret=None))
    assert r.status_code == 200, r.text
    assert r.json()["has_secret"] is False


@pytest.mark.parametrize("body,code", [
    (_body(url="ftp://example.org/"), "bad_lrs"),
    (_body(url="https://user:pass@example.org/"), "bad_lrs"),
    (_body(name="Bad Name"), "bad_name"),
    (_body(scope={"bogus": ["x"]}), "bad_scope"),
    (_body(xapi_version="9.9"), "bad_lrs"),
])
def test_create_validation(admin, body, code):
    r = admin.post("/api/admin/lrs", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code


def test_create_secret_requires_secret_key(app, db_exec):
    cfg_no_key = app.state.config.model_copy(update={"secret_key": None})

    async def go(s):
        await lrs_service.create_endpoint(s, cfg_no_key, name="n", url="https://e.org",
                                          secret="s", actor="t")

    with pytest.raises(ServiceError) as exc:
        db_exec(go)
    assert exc.value.code == "no_secret_key"

    async def go2(s):
        ep = await lrs_service.create_endpoint(s, cfg_no_key, name="n2", url="https://e.org/",
                                               secret=None, actor="t")
        assert ep.secret_enc is None
        assert ep.url == "https://e.org"

    db_exec(go2)


def test_encrypt_decrypt_roundtrip():
    key = Fernet.generate_key().decode()

    class Cfg:
        secret_key = key

    enc = lrs_service.encrypt_secret(Cfg(), "top-secret")
    ep = LrsEndpoint(name="t", url="https://e.org", secret_enc=enc)
    assert lrs_service.decrypt_secret(Cfg(), ep) == "top-secret"

    assert lrs_service.decrypt_secret(Cfg(), LrsEndpoint(name="t", url="https://e.org")) is None

    class NoKey:
        secret_key = None

    with pytest.raises(ServiceError) as exc:
        lrs_service.encrypt_secret(NoKey(), "x")
    assert exc.value.code == "no_secret_key"


def test_update_keeps_and_clears_secret(admin):
    created = admin.post("/api/admin/lrs", json=_body()).json()
    ep_id = created["id"]

    r = admin.patch(f"/api/admin/lrs/{ep_id}", json={"enabled": True, "url": "https://other.example.org/"})
    assert r.status_code == 200, r.text
    assert r.json()["has_secret"] is True  # unchanged
    assert r.json()["url"] == "https://other.example.org"

    r = admin.patch(f"/api/admin/lrs/{ep_id}", json={"secret": ""})
    assert r.status_code == 200, r.text
    assert r.json()["has_secret"] is False

    r = admin.patch(f"/api/admin/lrs/{ep_id}", json={"secret": "new-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["has_secret"] is True

    r = admin.patch(f"/api/admin/lrs/{ep_id}", json={"name": "Nope Name"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_name"

    r = admin.patch(f"/api/admin/lrs/{ep_id}", json={"auth_type": "bearer"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_lrs"

    assert admin.patch("/api/admin/lrs/00000000-0000-0000-0000-000000000000", json={"enabled": True}).status_code == 404


def test_delete(admin, db_exec):
    created = admin.post("/api/admin/lrs", json=_body()).json()
    ep_id = created["id"]

    assert admin.request("DELETE", f"/api/admin/lrs/{ep_id}").json() == {"ok": True}
    assert admin.get("/api/admin/lrs").json()["endpoints"] == []

    assert admin.request("DELETE", f"/api/admin/lrs/{ep_id}").status_code == 404


def test_audit_rows_contain_no_secret(admin, db_exec):
    created = admin.post("/api/admin/lrs", json=_body(secret="s3cret")).json()
    ep_id = created["id"]
    admin.patch(f"/api/admin/lrs/{ep_id}", json={"enabled": True, "secret": ""})
    admin.request("DELETE", f"/api/admin/lrs/{ep_id}")

    async def go(session):
        rows = list((await session.execute(
            select(AuditLog)
            .where(AuditLog.action.in_(("lrs_create", "lrs_update", "lrs_delete")))
            .order_by(AuditLog.id)
        )).scalars())
        assert [r.action for r in rows] == ["lrs_create", "lrs_update", "lrs_delete"]
        for r in rows:
            assert "s3cret" not in str(r.detail)
            assert "secret_enc" not in str(r.detail)

    db_exec(go)


def test_connection_via_mock_transport(app, http, admin, db_exec):
    ep_id = admin.post("/api/admin/lrs", json=_body(url="http://mock.lrs/", secret="s3cret")).json()["id"]

    async def fetch(s):
        return await lrs_service.get_endpoint(s, ep_id)
    endpoint = db_exec(fetch)

    def check(handler):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await lrs_service.test_connection(app.state.config, endpoint, client)
        return http.portal.call(run)

    result = check(lambda _req: httpx.Response(200, json={"version": ["2.0.0"]}))
    assert result == {"ok": True, "status": 200, "versions": ["2.0.0"], "error": None}

    result = check(lambda _req: httpx.Response(401))
    assert result == {"ok": False, "status": 401, "versions": None, "error": "HTTP 401"}

    def failing(_req):
        raise httpx.ConnectError("cannot connect")

    result = check(failing)
    assert result["ok"] is False
    assert result["status"] is None
    assert "cannot connect" in result["error"]


def test_lrs_endpoints_require_admin(http, admin, mesh):
    z3 = mesh.member("z3-r0")
    z3.login()

    assert http.get("/api/admin/lrs").status_code == 401
    assert http.post("/api/admin/lrs", json=_body()).status_code == 401
    assert http.get("/api/admin/lrs", headers={"Authorization": f"Bearer {z3.token}"}).status_code == 401

    del admin.headers["x-ltms-admin"]
    assert admin.post("/api/admin/lrs", json=_body()).status_code == 403
    assert admin.post("/api/admin/lrs/00000000-0000-0000-0000-000000000000/test").status_code == 403


def test_settings_never_returns_secrets(admin):
    body = admin.get("/api/admin/settings").json()
    for k in body:
        assert "password" not in k and "secret" not in k and not k.endswith("_key")
    assert "database_url" not in body


def test_create_missing_fields_and_bad_patch_are_422(admin):
    r = admin.post("/api/admin/lrs", json={})
    assert r.status_code == 422
    r = admin.post("/api/admin/lrs", json={"name": "x-lrs", "url": "https://lrs.example.org/xapi"})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    assert admin.request("PATCH", f"/api/admin/lrs/{eid}", json={"enabled": "yes"}).status_code == 422
    # unknown keys (including "actor" or "cursor") are ignored, not a 500
    r = admin.request("PATCH", f"/api/admin/lrs/{eid}", json={"actor": "x", "cursor": 99, "enabled": True})
    assert r.status_code == 200 and r.json()["cursor"] == 0 and r.json()["enabled"] is True
