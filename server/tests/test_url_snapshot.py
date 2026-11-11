"""URL datasets: one-time snapshots, guarded against SSRF (issue #249)."""
from __future__ import annotations

import httpx
import pytest

from server.services.url_snapshot import _key_for, host_allowed
from server.tests.test_datasets_read import data_dir  # noqa: F401

PAGE = b"<html><head><title>Docking</title></head><body><p>Approach at half speed.</p></body></html>"
PUBLIC = {"docs.example.org": ["93.184.216.34"], "cdn.example.org": ["93.184.216.35"],
          "evil.example.net": ["93.184.216.36"], "intranet.example.org": ["10.0.0.5"],
          "mixed.example.org": ["93.184.216.34", "127.0.0.1"], "v6.example.org": ["::ffff:192.168.1.1"]}


@pytest.fixture
def web(app):
    """A fake internet: handlers by URL, and a resolver from PUBLIC."""
    routes: dict[str, httpx.Response] = {}
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    async def resolve(host):
        if host not in PUBLIC:
            raise OSError("no such host")
        return PUBLIC[host]

    app.state.url_snapshot_transport = httpx.MockTransport(handler)
    app.state.url_snapshot_resolve = resolve
    app.state.config.dataset_url_allow = "docs.example.org, *.example.org"
    return routes, seen


@pytest.fixture
def ds(admin):
    return admin.post("/api/admin/datasets", json={"name": "ROS docs", "kind": "url", "licence": "CC BY 3.0"}).json()["id"]


def _snap(admin, ds, url):
    return admin.post(f"/api/admin/datasets/{ds}/snapshot", json={"url": url})


def test_an_allowed_url_makes_a_pending_version(admin, ds, web):
    routes, _ = web
    routes["https://docs.example.org/guides/docking"] = httpx.Response(
        200, content=PAGE, headers={"content-type": "text/html; charset=utf-8"})
    r = _snap(admin, ds, "https://docs.example.org/guides/docking")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"] == 1 and out["source_url"] == "https://docs.example.org/guides/docking"
    assert out["item"]["key"] == "docs.example.org/docking.html" and out["item"]["tier"] == "text"
    v = out["dataset"]["versions"][0]
    assert v["status"] == "pending" and v["source_url"] and v["snapshot_at"]
    items = admin.get(f"/api/admin/datasets/{ds}/versions/1/items").json()["items"]
    assert [i["key"] for i in items] == ["docs.example.org/docking.html"]
    # A second snapshot waits for the first to be approved, then makes version 2.
    assert _snap(admin, ds, "https://docs.example.org/guides/docking").json()["error"]["code"] == "version_pending"
    admin.post(f"/api/admin/datasets/{ds}/versions/1/approve")
    assert _snap(admin, ds, "https://docs.example.org/guides/docking").json()["version"] == 2


def test_redirects_to_allowed_hosts_are_followed(admin, ds, web):
    routes, seen = web
    routes["https://docs.example.org/a"] = httpx.Response(302, headers={"location": "https://cdn.example.org/a.md"})
    routes["https://cdn.example.org/a.md"] = httpx.Response(200, content=b"# A", headers={"content-type": "text/markdown"})
    r = _snap(admin, ds, "https://docs.example.org/a")
    assert r.status_code == 200, r.text
    assert r.json()["source_url"] == "https://cdn.example.org/a.md" and r.json()["item"]["key"] == "cdn.example.org/a.md"


@pytest.mark.parametrize("url,code", [
    ("http://docs.example.org/x", "bad_url"),
    ("https://evil.example.net/x", "host_not_allowed"),
    ("https://intranet.example.org/x", "private_address"),
    ("https://mixed.example.org/x", "private_address"),
    ("https://v6.example.org/x", "private_address"),
    ("https://user:pw@docs.example.org/x", "bad_url"),
    ("https://nowhere.example.org/x", "unresolvable"),
])
def test_unsafe_urls_are_rejected(admin, ds, web, url, code):
    _, seen = web
    r = _snap(admin, ds, url)
    assert r.json()["error"]["code"] == code, r.text
    assert seen == []  # nothing was fetched


def test_a_redirect_to_a_disallowed_or_private_host_is_rejected(admin, ds, web):
    routes, seen = web
    routes["https://docs.example.org/r1"] = httpx.Response(301, headers={"location": "https://evil.example.net/"})
    routes["https://docs.example.org/r2"] = httpx.Response(301, headers={"location": "https://intranet.example.org/"})
    routes["https://docs.example.org/r3"] = httpx.Response(301, headers={"location": "http://docs.example.org/"})
    assert _snap(admin, ds, "https://docs.example.org/r1").json()["error"]["code"] == "host_not_allowed"
    assert _snap(admin, ds, "https://docs.example.org/r2").json()["error"]["code"] == "private_address"
    assert _snap(admin, ds, "https://docs.example.org/r3").json()["error"]["code"] == "bad_url"
    assert all("evil" not in u and "intranet" not in u for u in seen)


def test_redirect_loops_stop(admin, ds, web):
    routes, _ = web
    routes["https://docs.example.org/loop"] = httpx.Response(302, headers={"location": "/loop"})
    assert _snap(admin, ds, "https://docs.example.org/loop").json()["error"]["code"] == "too_many_redirects"


def test_size_cap(admin, app, ds, web):
    routes, _ = web
    app.state.config.dataset_text_max_mb = 1
    routes["https://docs.example.org/big"] = httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
    assert _snap(admin, ds, "https://docs.example.org/big").json()["error"]["code"] == "too_large"


def test_empty_allow_list_is_off(admin, app, ds, web):
    app.state.config.dataset_url_allow = ""
    r = _snap(admin, ds, "https://docs.example.org/x")
    assert r.status_code == 403 and r.json()["error"]["code"] == "url_disabled"
    assert "LTMS_DATASET_URL_ALLOW" in r.json()["error"]["message"]


def test_only_url_datasets(admin, web):
    did = admin.post("/api/admin/datasets", json={"name": "Up", "kind": "upload", "licence": "x"}).json()["id"]
    assert _snap(admin, did, "https://docs.example.org/x").json()["error"]["code"] == "not_url"


def test_host_rules_and_keys():
    assert host_allowed("docs.ros.org", ["docs.ros.org"])
    assert host_allowed("a.b.example.org", ["*.example.org"])
    assert not host_allowed("example.org", ["*.example.org"])
    assert not host_allowed("evilexample.org", ["*.example.org"])
    assert not host_allowed("docs.ros.org.evil.com", ["docs.ros.org"])
    assert _key_for("https://docs.example.org/", "text/html") == "docs.example.org/index.html"
    assert _key_for("https://docs.example.org/a b?x=1", "text/plain") == "docs.example.org/a_b.txt"
