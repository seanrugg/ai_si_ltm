"""The city database for network estimates, managed from Settings (issue #388)."""
from __future__ import annotations

import gzip
import io
import json
import struct
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from server.models import AuditLog
from server.services import geoip_source, location
from server.tests.test_datasets_read import data_dir  # noqa: F401

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
THIS_MONTH = geoip_source.dbip_url(2026, 11)


# --------------------------------------------------------------------------- #
# A tiny MMDB writer: one node, every IPv4 address maps to one record.
# --------------------------------------------------------------------------- #
def _ctrl(type_: int, size: int) -> bytes:
    if type_ <= 7:
        head = bytes([(type_ << 5) | size])
        return head
    return bytes([size, type_ - 7])


def _enc(v) -> bytes:
    if isinstance(v, dict):
        out = _ctrl(7, len(v))
        for k, x in v.items():
            out += _enc(str(k)) + _enc(x)
        return out
    if isinstance(v, list):
        return _ctrl(11, len(v)) + b"".join(_enc(x) for x in v)
    if isinstance(v, str):
        b = v.encode()
        assert len(b) < 29
        return _ctrl(2, len(b)) + b
    if isinstance(v, float):
        return _ctrl(3, 8) + struct.pack(">d", v)
    if isinstance(v, tuple):  # (type, int): libmaxminddb checks metadata types strictly
        t, n = v
        b = n.to_bytes(8, "big").lstrip(b"\0")
        return _ctrl(t, len(b)) + b
    if isinstance(v, int):
        return _enc((5 if v < 2 ** 16 else 6, v))
    raise TypeError(v)


def make_mmdb(dbtype="DBIP-City-Lite", city="Testville", built=1_793_000_000) -> bytes:
    record = {"city": {"names": {"en": city}}, "country": {"names": {"en": "Testland"}},
              "location": {"latitude": 38.5, "longitude": -77.4, "accuracy_radius": 20}}
    node_count = 1
    pointer = node_count + 16  # the first byte of the data section
    tree = pointer.to_bytes(3, "big") * 2  # record size 24: left and right both point at the record
    data = _enc(record)
    u16, u32, u64 = 5, 6, 9
    meta = {"node_count": (u32, node_count), "record_size": (u16, 24), "ip_version": (u16, 4),
            "database_type": dbtype, "languages": ["en"], "binary_format_major_version": (u16, 2),
            "binary_format_minor_version": (u16, 0), "build_epoch": (u64, built), "description": {"en": "test"}}
    return tree + b"\0" * 16 + data + b"\xab\xcd\xefMaxMind.com" + _enc(meta)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def web(app):
    routes: dict[str, httpx.Response] = {}
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    async def resolve(host):
        return {"download.db-ip.com": ["93.184.216.34"], "elsewhere.example": ["93.184.216.35"]}.get(host) or \
            (_ for _ in ()).throw(OSError("no host"))

    app.state.url_snapshot_transport = httpx.MockTransport(handler)
    app.state.url_snapshot_resolve = resolve
    routes["_seen"] = seen  # type: ignore[assignment]
    return routes


def _publish(routes, url, body: bytes):
    routes[url] = httpx.Response(200, content=body, headers={"content-type": "application/gzip"})


def _audit(db_exec, action):
    async def q(session):
        return list(await session.scalars(select(AuditLog).where(AuditLog.action == action)))
    return db_exec(q)


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def test_off_by_default(admin, data_dir):  # noqa: F811
    s = admin.get("/api/admin/geoip").json()
    assert s["mode"] == "off" and s["on"] is False and s["attribution"] is False
    assert "ip-to-city-lite" in s["manual_url"]
    geo = admin.get("/api/admin/locations").json()["geo"]
    assert geo["enabled"] is False and "Settings" in geo["reason"]


def test_download_turns_estimates_on(admin, app, web, data_dir, db_exec):  # noqa: F811
    _publish(web, geoip_source._candidates(datetime.now(timezone.utc))[0], gzip.compress(make_mmdb()))
    r = admin.post("/api/admin/geoip/download")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["mode"] == "download" and s["on"] is True and s["provider"] == "DB-IP"
    assert s["licence"] == "CC BY 4.0" and s["attribution"] is True
    assert s["build_date"] and s["size"] > 0 and s["last_check"] and s["next_check"]
    assert s["database_type"] == "DBIP-City-Lite" and s["source"].startswith("https://download.db-ip.com/free/")
    # Lookups now read the managed file.
    path = geoip_source.active_path(app.state.config)
    assert path and path.endswith("geoip/city.mmdb")
    hit = location.lookup_city(path, "8.8.8.8")
    assert hit["label"] == "Testville, Testland" and hit["lat"] == 38.5
    geo = admin.get("/api/admin/locations").json()["geo"]
    assert geo["enabled"] is True and geo["attribution"] is True and geo["credit_url"] == "https://db-ip.com"
    rows = _audit(db_exec, "geoip_update")
    assert len(rows) == 1 and rows[0].detail["provider"] == "DB-IP"


def test_download_falls_back_to_last_month(admin, web, data_dir):  # noqa: F811
    _publish(web, geoip_source._candidates(datetime.now(timezone.utc))[1], gzip.compress(make_mmdb()))
    r = admin.post("/api/admin/geoip/download")
    assert r.status_code == 200, r.text


def test_corrupt_or_wrong_type_keeps_the_old_file(admin, app, web, data_dir):  # noqa: F811
    cands = geoip_source._candidates(datetime.now(timezone.utc))
    _publish(web, cands[0], gzip.compress(make_mmdb(city="Firstville")))
    assert admin.post("/api/admin/geoip/download").status_code == 200
    path = geoip_source.active_path(app.state.config)

    _publish(web, cands[0], gzip.compress(b"not a database at all"))
    r = admin.post("/api/admin/geoip/download")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_file"
    _publish(web, cands[0], gzip.compress(make_mmdb(dbtype="DBIP-Country-Lite")))
    r = admin.post("/api/admin/geoip/download")
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_city"
    _publish(web, cands[0], b"\x1f\x8b broken gzip")
    assert admin.post("/api/admin/geoip/download").status_code == 422

    assert location.lookup_city(path, "8.8.8.8")["label"] == "Firstville, Testland"
    s = admin.get("/api/admin/geoip").json()
    assert s["on"] is True and s["error"]


def test_refuses_other_hosts_and_redirects_away(admin, web, data_dir):  # noqa: F811
    cands = geoip_source._candidates(datetime.now(timezone.utc))
    web[cands[0]] = httpx.Response(302, headers={"location": "https://elsewhere.example/city.mmdb.gz"})
    r = admin.post("/api/admin/geoip/download")
    assert r.status_code == 403 and r.json()["error"]["code"] == "host_not_allowed"
    assert not any("elsewhere" in u for u in web["_seen"])


def test_redirect_within_dbip_is_followed(admin, web, data_dir):  # noqa: F811
    cands = geoip_source._candidates(datetime.now(timezone.utc))
    moved = "https://download.db-ip.com/free/mirror/city.mmdb.gz"
    web[cands[0]] = httpx.Response(302, headers={"location": moved})
    _publish(web, moved, gzip.compress(make_mmdb()))
    assert admin.post("/api/admin/geoip/download").status_code == 200


def test_no_network_points_to_upload(admin, app, data_dir):  # noqa: F811
    def down(request):
        raise httpx.ConnectError("no route")

    async def resolve(host):
        return ["93.184.216.34"]
    app.state.url_snapshot_transport = httpx.MockTransport(down)
    app.state.url_snapshot_resolve = resolve
    r = admin.post("/api/admin/geoip/download")
    assert r.status_code == 502 and "Upload" in r.json()["error"]["message"]


def test_decompression_cap(app, data_dir, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(geoip_source, "MAX_UNCOMPRESSED_MB", 1)
    src = tmp_path / "bomb.gz"
    src.write_bytes(gzip.compress(b"\0" * (3 * 1024 * 1024)))
    with pytest.raises(geoip_source.ServiceError) as exc:
        geoip_source.gunzip_capped(src, tmp_path / "out", 1024 * 1024)
    assert exc.value.code == "too_large"


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("gz", [False, True])
def test_upload_mmdb_or_gz(admin, app, data_dir, db_exec, gz):  # noqa: F811
    body = make_mmdb(city="Uploadville")
    name = "city.mmdb"
    if gz:
        body, name = gzip.compress(body), "city.mmdb.gz"
    r = admin.post("/api/admin/geoip/upload", files={"file": (name, io.BytesIO(body))},
                   data={"provider": "MaxMind", "licence": "GeoLite2 EULA"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["mode"] == "upload" and s["provider"] == "MaxMind" and s["licence"] == "GeoLite2 EULA"
    assert s["attribution"] is False and s["next_check"] is None  # not DB-IP; uploads aren't refreshed
    assert location.lookup_city(geoip_source.active_path(app.state.config), "1.1.1.1")["label"].startswith("Upload")
    assert _audit(db_exec, "geoip_update")[0].detail["file"] == name


def test_upload_needs_a_provider_and_a_real_file(admin, data_dir):  # noqa: F811
    r = admin.post("/api/admin/geoip/upload", files={"file": ("x.mmdb", io.BytesIO(make_mmdb()))},
                   data={"provider": " "})
    assert r.status_code == 422 and r.json()["error"]["code"] == "provider_required"
    r = admin.post("/api/admin/geoip/upload", files={"file": ("x.mmdb", io.BytesIO(b"junk"))},
                   data={"provider": "DB-IP"})
    assert r.status_code == 422


def test_upload_size_cap(admin, data_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(geoip_source, "MAX_UNCOMPRESSED_MB", 1)
    r = admin.post("/api/admin/geoip/upload", files={"file": ("big.mmdb", io.BytesIO(b"\0" * (2 * 1024 * 1024)))},
                   data={"provider": "DB-IP"})
    assert r.status_code == 413


def test_member_cannot_manage(http, data_dir):  # noqa: F811
    assert http.get("/api/admin/geoip").status_code in (401, 403)
    assert http.post("/api/admin/geoip/download").status_code in (401, 403)


# --------------------------------------------------------------------------- #
# Off, environment override, refresh
# --------------------------------------------------------------------------- #
def test_turn_off_keeps_or_deletes(admin, app, web, data_dir, db_exec):  # noqa: F811
    _publish(web, geoip_source._candidates(datetime.now(timezone.utc))[0], gzip.compress(make_mmdb()))
    admin.post("/api/admin/geoip/download")
    s = admin.post("/api/admin/geoip/off", json={}).json()
    assert s["mode"] == "off" and s["on"] is False and s["file_kept"] is True
    assert geoip_source.active_path(app.state.config) is None
    s = admin.post("/api/admin/geoip/off", json={"delete": True}).json()
    assert s["file_kept"] is False and s["provider"] is None
    assert len(_audit(db_exec, "geoip_off")) == 2


def test_environment_wins_and_turns_controls_off(admin, app, data_dir):  # noqa: F811
    app.state.config.geoip_db_path = "/opt/ltms/geo/city.mmdb"
    s = admin.get("/api/admin/geoip").json()
    assert s["mode"] == "env" and s["env"] is True and s["attribution"] is False
    for url in ("/api/admin/geoip/download", "/api/admin/geoip/off"):
        r = admin.post(url, json={})
        assert r.status_code == 409 and r.json()["error"]["code"] == "env_set"
    assert geoip_source.active_path(app.state.config) == "/opt/ltms/geo/city.mmdb"


def test_refresh_job(app, web, data_dir, db_exec):  # noqa: F811
    cfg = app.state.config
    kw = {"transport": app.state.url_snapshot_transport, "resolve": app.state.url_snapshot_resolve}

    assert db_exec(_runner(geoip_source.refresh, cfg, NOW, kw)) == "skipped: not in download mode"
    _publish(web, THIS_MONTH, gzip.compress(make_mmdb(city="Oct")))
    assert db_exec(_runner(geoip_source.refresh, cfg, NOW, kw)) == "skipped: not in download mode"

    # Turn on as of NOW, then: not due tomorrow; due after 30 days.
    async def on(session):
        return await geoip_source.download(session, cfg, admin="admin:test", now=NOW, **kw)
    db_exec(on)
    assert json.loads((geoip_source.geo_dir(cfg) / "state.json").read_text())["next_check"].startswith("2026-12-14")
    assert db_exec(_runner(geoip_source.refresh, cfg, NOW.replace(day=15), kw)) == "skipped: not due"

    dec = datetime(2026, 12, 15, tzinfo=timezone.utc)
    _publish(web, geoip_source.dbip_url(2026, 12), gzip.compress(b"broken"))
    assert db_exec(_runner(geoip_source.refresh, cfg, dec, kw)).startswith("failed")
    path = geoip_source.active_path(cfg)
    assert location.lookup_city(path, "8.8.8.8")["label"].startswith("Oct")  # old file kept

    _publish(web, geoip_source.dbip_url(2026, 12), gzip.compress(make_mmdb(city="Dec")))
    assert db_exec(_runner(geoip_source.refresh, cfg, dec.replace(day=16), kw)).startswith("refreshed")
    assert location.lookup_city(path, "8.8.8.8")["label"].startswith("Dec")


def _runner(fn, cfg, now, kw):
    async def go(session):
        return await fn(session, cfg, now=now, **kw)
    return go
