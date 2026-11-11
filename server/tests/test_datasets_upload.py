"""Uploading text files into a dataset version (issue #237)."""
from __future__ import annotations

from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data" / "datasets"


@pytest.fixture(autouse=True)
def data_dir(app, tmp_path):
    app.state.config.data_dir = str(tmp_path / "data")
    app.state.config.dataset_text_max_mb = 1
    return tmp_path / "data"


@pytest.fixture
def ds(admin):
    r = admin.post("/api/admin/datasets", json={"name": "Docs", "kind": "upload", "licence": "Internal"})
    return r.json()["id"]


def _upload(admin, ds, *names, prefix=None, version=1, content=None):
    files = [("files", (n, content if content is not None else (DATA / n).read_bytes())) for n in names]
    data = {"prefix": prefix} if prefix else {}
    return admin.post(f"/api/admin/datasets/{ds}/versions/{version}/items", files=files, data=data)


def test_every_text_type_extracts(admin, ds):
    names = ["notes.txt", "drive.md", "page.html", "sensors.csv", "battery.tsv", "rows.json", "rows.jsonl",
             "robot.yaml", "robot.xml"]
    r = _upload(admin, ds, *names, prefix="manuals")
    assert r.status_code == 200, r.text
    got = {i["key"]: i for i in r.json()["items"]}
    assert set(got) == {f"manuals/{n}" for n in names}
    assert got["manuals/drive.md"]["media_type"] == "text/markdown"
    assert got["manuals/sensors.csv"]["meta"] == {"columns": ["sensor", "unit", "max"], "rows": 2,
                                                  "sample": [["front-tof", "mm", "2000"], ["imu0", "m/s2", "16"]]}
    assert got["manuals/battery.tsv"]["meta"]["columns"] == ["sensor", "unit"]
    assert got["manuals/rows.json"]["meta"]["rows"] == 2
    assert got["manuals/rows.jsonl"]["meta"]["rows"] == 3
    assert got["manuals/robot.yaml"]["meta"] == {"top": ["robot"]}
    assert got["manuals/robot.xml"]["meta"] == {"root": "robot", "children": 2}
    ds_after = r.json()["dataset"]["versions"][0]
    assert ds_after["item_count"] == 9 and ds_after["manifest_hash"]


def test_html_keeps_visible_text_only(admin, ds, app, db_exec):
    from sqlalchemy import select

    from server.models import DatasetItem
    _upload(admin, ds, "page.html")

    async def text(session):
        return await session.scalar(select(DatasetItem.text).where(DatasetItem.key == "page.html"))
    assert db_exec(text) == "Title\nVisible text"


def test_content_not_extension_decides(admin, ds):
    r = _upload(admin, ds, "fake.txt")
    assert r.status_code == 415 and "image/png" in r.json()["error"]["message"]


def test_same_content_stored_once_and_manifest_is_stable(admin, ds, data_dir):
    _upload(admin, ds, "notes.txt")
    first = admin.get(f"/api/admin/datasets/{ds}").json()["versions"][0]["manifest_hash"]
    _upload(admin, ds, "notes.txt", prefix="copy")
    files = [p for p in (data_dir / "datasets").rglob("*") if p.is_file()]
    assert len(files) == 1
    assert admin.get(f"/api/admin/datasets/{ds}").json()["versions"][0]["manifest_hash"] != first
    items = admin.get(f"/api/admin/datasets/{ds}/versions/1/items").json()["items"]
    assert [i["key"] for i in items] == ["copy/notes.txt", "notes.txt"]


def test_limits_and_locks(admin, ds):
    assert _upload(admin, ds, "big.txt", content=b"x" * (1024 * 1024 + 1)).status_code == 413
    assert _upload(admin, ds, "notes.txt").status_code == 200
    assert _upload(admin, ds, "notes.txt").json()["error"]["code"] == "key_exists"
    assert _upload(admin, ds, "notes.txt", prefix="../up").json()["error"]["code"] == "bad_key"
    admin.post(f"/api/admin/datasets/{ds}/versions/1/approve")
    assert _upload(admin, ds, "drive.md").json()["error"]["code"] == "version_locked"


def test_xml_entities_are_not_expanded(admin, ds):
    bomb = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa">]><l>&a;</l>'
    r = _upload(admin, ds, "bomb.xml", content=bomb)
    assert r.status_code == 200
    assert "parse_error" in r.json()["items"][0]["meta"]
