"""Reference sources imported from the dashboard (issue #351)."""
from __future__ import annotations

import json

import httpx
import pytest

from server.services import reference_sources
from server.tests.test_datasets_read import _get, data_dir  # noqa: F401
from server.tests.test_import_glossary import doc

URL = "https://id.i2idl.org/glossary.jsonld"


@pytest.fixture
def web(app):
    routes: dict[str, httpx.Response] = {}

    def handler(request):
        return routes.get(str(request.url), httpx.Response(404))

    async def resolve(host):
        return {"id.i2idl.org": ["93.184.216.34"]}.get(host) or (_ for _ in ()).throw(OSError("no host"))

    app.state.url_snapshot_transport = httpx.MockTransport(handler)
    app.state.url_snapshot_resolve = resolve
    app.state.config.dataset_url_allow = ""  # not needed: the source's host is fixed in code
    return routes


def _publish(routes, version):
    routes[URL] = httpx.Response(200, content=json.dumps(doc(version)).encode(),
                                 headers={"content-type": "application/ld+json"})


def test_listed_before_import(admin):
    [s] = admin.get("/api/admin/reference-sources").json()["sources"]
    assert s["key"] == "i2idl-glossary" and s["dataset_id"] is None and s["publications"] == []
    assert s["refresh_hours"] == 168


def test_import_latest_then_unchanged_then_new_publication(admin, mesh, web):
    _publish(web, "v0.0.93")
    r = admin.post("/api/admin/reference-sources/i2idl-glossary/import")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "imported" and out["version"] == 1 and out["items"] == 2
    ds = out["dataset_id"]
    keys = sorted(i["key"] for i in admin.get(f"/api/admin/datasets/{ds}/versions/1/items").json()["items"])
    assert keys == ["concepts/learning-record.json", "concepts/xapi-statement.json", "publication/v0.0.93.json"]

    assert admin.post("/api/admin/reference-sources/i2idl-glossary/import").json()["status"] == "unchanged"
    [s] = admin.get("/api/admin/reference-sources").json()["sources"]
    assert s["publications"] == [{"publicationVersion": "v0.0.93", "version": 1, "status": "pending"}]

    # A new publication while v1 waits is refused, not stacked.
    _publish(web, "v0.0.94")
    assert admin.post("/api/admin/reference-sources/i2idl-glossary/import").json()["error"]["code"] == "version_pending"
    admin.post(f"/api/admin/datasets/{ds}/versions/1/approve")
    assert admin.post("/api/admin/reference-sources/i2idl-glossary/import").json()["version"] == 2

    z3 = mesh.member("z3-r0")
    hits = _get(z3, f"/api/datasets/{ds}/search", q="machine-readable record").json()["hits"]
    assert hits[0]["key"] == "concepts/learning-record.json"


def test_import_from_file_needs_no_network(admin, app):
    app.state.url_snapshot_transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("network")))
    r = admin.post("/api/admin/reference-sources/i2idl-glossary/import-file",
                   files={"file": ("glossary.jsonld", json.dumps(doc()).encode(), "application/ld+json")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "imported"
    bad = admin.post("/api/admin/reference-sources/i2idl-glossary/import-file",
                     files={"file": ("x.json", b"{\"@graph\": []}", "application/json")})
    assert bad.json()["error"]["code"] == "bad_source"
    assert admin.post("/api/admin/reference-sources/nope/import").status_code == 404


def test_unreachable_says_to_use_a_file(admin, web):
    r = admin.post("/api/admin/reference-sources/i2idl-glossary/import")  # 404 from the fake host
    assert r.json()["error"]["code"] == "fetch_failed"


def test_refresh_only_touches_imported_sources(app, admin, web, db_exec):
    async def refresh(session):
        return await reference_sources.refresh_all(session, app.state.config,
                                                   transport=app.state.url_snapshot_transport,
                                                   resolve=app.state.url_snapshot_resolve)
    _publish(web, "v0.0.93")
    assert db_exec(refresh) == {}  # never imported: nothing fetched
    admin.post("/api/admin/reference-sources/i2idl-glossary/import")
    assert db_exec(refresh) == {"i2idl-glossary": "unchanged"}
