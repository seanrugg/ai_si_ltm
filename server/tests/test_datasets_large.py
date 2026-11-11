"""Large and resumable dataset uploads; serving files with ranges (issue #245)."""
from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from server.models import DatasetUpload
from server.services import dataset_uploads
from server.tests.test_datasets_read import _get, _reads, data_dir  # noqa: F401

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    monkeypatch.setattr(dataset_uploads, "CHUNK_BYTES", 1000)


@pytest.fixture
def ds(admin):
    return admin.post("/api/admin/datasets", json={"name": "Bags", "kind": "upload", "licence": "Internal"}).json()["id"]


def _base(ds, version=1):
    return f"/api/admin/datasets/{ds}/versions/{version}/items/uploads"


def _start(admin, ds, name, size, prefix=None):
    return admin.post(_base(ds), json={"filename": name, "size": size, "prefix": prefix})


def _put(admin, ds, uid, data, first, total):
    return admin.put(f"{_base(ds)}/{uid}", content=data,
                     headers={"Content-Range": f"bytes {first}-{first + len(data) - 1}/{total}"})


def _payload(n=2500):
    return b"\x89HDF\r\n\x1a\n" + os.urandom(n - 8)  # sniffs as hdf5: a large-tier file


def test_three_chunks_reassemble_with_the_right_hash(admin, ds):
    data = _payload()
    r = _start(admin, ds, "run1.h5", len(data))
    assert r.status_code == 201, r.text
    up = r.json()
    assert up["chunk_bytes"] == 1000 and up["received_bytes"] == 0
    for first in (0, 1000, 2000):
        r = _put(admin, ds, up["upload_id"], data[first:first + 1000], first, len(data))
        assert r.status_code == 200, r.text
    assert r.json()["received_bytes"] == len(data)
    r = admin.post(f"{_base(ds)}/{up['upload_id']}/finish")
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["sha256"] == hashlib.sha256(data).hexdigest()
    assert item["tier"] == "large" and item["media_type"] == "application/x-hdf5"
    assert r.json()["dataset"]["versions"][0]["item_count"] == 1
    assert admin.get(f"{_base(ds)}/{up['upload_id']}").status_code == 404  # finished uploads are gone


def test_a_resumed_upload_continues(admin, ds):
    data = _payload()
    uid = _start(admin, ds, "run2.h5", len(data)).json()["upload_id"]
    _put(admin, ds, uid, data[:1000], 0, len(data))
    # The client lost its place: a wrong offset is refused and says where to resume.
    r = _put(admin, ds, uid, data[2000:], 2000, len(data))
    assert r.status_code == 409 and r.json()["error"]["code"] == "wrong_offset"
    assert admin.get(f"{_base(ds)}/{uid}").json()["received_bytes"] == 1000
    assert admin.post(f"{_base(ds)}/{uid}/finish").json()["error"]["code"] == "incomplete"
    _put(admin, ds, uid, data[1000:2000], 1000, len(data))
    _put(admin, ds, uid, data[2000:], 2000, len(data))
    assert admin.post(f"{_base(ds)}/{uid}/finish").json()["item"]["sha256"] == hashlib.sha256(data).hexdigest()


def test_bad_chunks(admin, ds):
    uid = _start(admin, ds, "x.h5", 2500).json()["upload_id"]
    assert admin.put(f"{_base(ds)}/{uid}", content=b"abc").json()["error"]["code"] == "bad_range"
    assert _put(admin, ds, uid, b"a" * 1001, 0, 2500).json()["error"]["code"] == "chunk_too_large"
    r = admin.put(f"{_base(ds)}/{uid}", content=b"abc", headers={"Content-Range": "bytes 0-9/2500"})
    assert r.json()["error"]["code"] == "bad_range"  # the body is shorter than the range
    assert _put(admin, ds, uid, b"abc", 0, 9999).json()["error"]["code"] == "bad_range"


def test_above_the_max_is_413(admin, app, ds):
    app.state.config.dataset_upload_max_mb = 1
    r = _start(admin, ds, "huge.bag", MB + 1)
    assert r.status_code == 413 and "catalog integration" in r.json()["error"]["message"]
    assert _start(admin, ds, "", 10).json()["error"]["code"] == "bad_upload"
    assert _start(admin, ds, "ok.bag", 0).json()["error"]["code"] == "bad_upload"


def test_one_upload_per_key(admin, ds):
    assert _start(admin, ds, "a.h5", 10).status_code == 201
    assert _start(admin, ds, "a.h5", 10).json()["error"]["code"] == "upload_exists"


def test_stale_uploads_expire(admin, app, ds, db_exec):
    uid = _start(admin, ds, "old.h5", 2500).json()["upload_id"]
    part = dataset_uploads.part_path(app.state.config.data_dir, uid)
    assert part.exists()

    async def go(session):
        later = datetime.now(timezone.utc) + timedelta(hours=25)
        return await dataset_uploads.purge_stale(session, app.state.config, now=later)
    assert db_exec(go) == {"expired": 1}
    assert not part.exists()
    assert db_exec(lambda s: s.scalar(select(DatasetUpload.id))) is None


def _approved_large(admin, ds, data):
    uid = _start(admin, ds, "run.h5", len(data), prefix="logs").json()["upload_id"]
    for first in range(0, len(data), 1000):
        _put(admin, ds, uid, data[first:first + 1000], first, len(data))
    admin.post(f"{_base(ds)}/{uid}/finish")
    admin.post(f"/api/admin/datasets/{ds}/versions/1/approve")


def test_ranges_return_206_and_log_one_read(admin, mesh, ds, db_exec):
    data = _payload()
    _approved_large(admin, ds, data)
    z3 = mesh.member("z3-r0")
    z3.login()
    auth = {"Authorization": f"Bearer {z3.token}"}
    url = f"/api/datasets/{ds}/items/logs/run.h5/content"
    whole = z3.http.get(url, headers=auth)
    assert whole.status_code == 200, whole.text
    assert whole.status_code == 200 and whole.content == data
    assert whole.headers["etag"] == f'"{hashlib.sha256(data).hexdigest()}"'
    assert "immutable" in whole.headers["cache-control"]
    assert whole.headers["x-content-type-options"] == "nosniff"
    assert whole.headers["content-disposition"].startswith("attachment")
    for start, end in ((0, 99), (100, 1099), (2400, 2499)):
        r = z3.http.get(url, headers={**auth, "Range": f"bytes={start}-{end}"})
        assert r.status_code == 206 and r.content == data[start:end + 1]
    # The whole file and the range from 0 are two reads with no cycle; the other ranges aren't reads.
    assert len(_reads(db_exec)) == 2
    cycle = "5b0f6a3e-2c1d-4e8f-9a7b-1c2d3e4f5a6b"
    for start in (0, 1000, 0):
        z3.http.get(url, params={"cycle": cycle}, headers={**auth, "Range": f"bytes={start}-"})
    assert len(_reads(db_exec)) == 3  # one per member, item and cycle
    # The text endpoint still works for a large item: metadata, no text.
    item = _get(z3, f"/api/datasets/{ds}/items/logs/run.h5").json()
    assert item["tier"] == "large" and item["text"] is None


def test_content_needs_an_approved_item(admin, mesh, ds):
    z3 = mesh.member("z3-r0")
    z3.login()
    auth = {"Authorization": f"Bearer {z3.token}"}
    assert z3.http.get(f"/api/datasets/{ds}/items/nope/content", headers=auth).status_code == 404
