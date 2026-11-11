"""Archives unpack into dataset items, with guards (issue #246)."""
from __future__ import annotations

import gzip
import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from server.services import archives
from server.tests.test_datasets_read import data_dir  # noqa: F401


@pytest.fixture
def ds(admin):
    return admin.post("/api/admin/datasets", json={"name": "Bundles", "kind": "upload", "licence": "Internal"}).json()["id"]


def _upload(admin, ds, name, content, prefix=None):
    data = {"prefix": prefix} if prefix else None
    return admin.post(f"/api/admin/datasets/{ds}/versions/1/items", files=[("files", (name, content))], data=data)


def _items(admin, ds):
    return {i["key"]: i for i in admin.get(f"/api/admin/datasets/{ds}/versions/1/items").json()["items"]}


def _zip(entries: dict[str, bytes], *, links=(), compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in entries.items():
            z.writestr(name, data)
        for name, target in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, target)
    return buf.getvalue()


def _tgz(entries: dict[str, bytes], *, symlink=None, hardlink=None, device=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        for name, kind in ((symlink, tarfile.SYMTYPE), (hardlink, tarfile.LNKTYPE), (device, tarfile.CHRTYPE)):
            if name:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = "notes.txt"
                t.addfile(info)
    return buf.getvalue()


def test_a_mixed_zip_unpacks_with_the_right_keys(admin, ds):
    nested = _zip({"inner.txt": b"hi"})
    content = _zip({
        "manuals/drive.md": b"# Drive\nThe wheel stalls on carpet.",
        "data/torque.csv": b"joint,max_nm\nshoulder,12\n",
        "data/blob.bin": b"\x00\x01\x02binary",
        "nested.zip": nested,
        "manuals/": b"",
    })
    r = _upload(admin, ds, "bundle.zip", content, prefix="v1")
    assert r.status_code == 200, r.text
    summary = r.json()["items"][0]["archive"]
    assert sorted(i["key"] for i in summary["stored"]) == ["v1/data/blob.bin", "v1/data/torque.csv",
                                                          "v1/manuals/drive.md"]
    assert summary["skipped"] == [{"key": "v1/nested.zip", "reason": "nested archive (zip); upload it separately"}]
    assert summary["errors"] == []
    items = _items(admin, ds)
    assert set(items) == {"v1/data/blob.bin", "v1/data/torque.csv", "v1/manuals/drive.md"}
    assert items["v1/manuals/drive.md"]["tier"] == "text"
    assert items["v1/data/torque.csv"]["meta"]["rows"] == 1
    assert items["v1/data/blob.bin"]["tier"] == "large"


def test_a_tar_gz_unpacks_and_skips_existing_keys(admin, ds):
    assert _upload(admin, ds, "notes.txt", b"already here").status_code == 200
    r = _upload(admin, ds, "logs.tar.gz", _tgz({"notes.txt": b"dup", "run/log.txt": b"started"}))
    assert r.status_code == 200, r.text
    summary = r.json()["items"][0]["archive"]
    assert [i["key"] for i in summary["stored"]] == ["run/log.txt"]
    assert summary["skipped"] == [{"key": "notes.txt", "reason": "already in this version"}]


def test_the_archive_file_itself_is_not_kept(admin, ds, app):
    content = _zip({"a.txt": b"aaa"})
    _upload(admin, ds, "one.zip", content)
    files = [p for p in (Path(app.state.config.data_dir) / "datasets").rglob("*") if p.is_file()]
    assert len(files) == 1  # just a.txt


@pytest.mark.parametrize("content,why", [
    (_zip({"../evil.txt": b"x"}), "'..'"),
    (_zip({"/etc/passwd": b"x"}), "absolute path"),
    (_zip({"ok.txt": b"x"}, links=[("link", "/etc/passwd")]), "symlink"),
    (_tgz({"ok.txt": b"x"}, symlink="link"), "link"),
    (_tgz({"ok.txt": b"x"}, hardlink="hard"), "link"),
    (_tgz({"ok.txt": b"x"}, device="dev"), "special file"),
    (_tgz({"a/../../evil": b"x"}), "'..'"),
])
def test_unsafe_archives_are_rejected_whole(admin, ds, content, why):
    r = _upload(admin, ds, "bad.zip" if content.startswith(b"PK") else "bad.tgz", content)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "bad_archive" and why in r.json()["error"]["message"]
    assert _items(admin, ds) == {}


def test_a_zip_bomb_is_rejected(admin, ds):
    bomb = _zip({"zeros.bin": b"\x00" * (8 * 1024 * 1024)})
    assert len(bomb) * archives.MAX_RATIO < 8 * 1024 * 1024
    r = _upload(admin, ds, "bomb.zip", bomb)
    assert r.status_code == 422 and "ratio" in r.json()["error"]["message"]
    assert _items(admin, ds) == {}


def test_too_many_entries_and_too_big(admin, ds, app, monkeypatch):
    monkeypatch.setattr(archives, "MAX_ENTRIES", 3)
    r = _upload(admin, ds, "many.zip", _zip({f"f{i}.txt": b"x" for i in range(4)}))
    assert r.json()["error"]["code"] == "bad_archive" and "entries" in r.json()["error"]["message"]
    r = _upload(admin, ds, "many.tgz", _tgz({f"f{i}.txt": b"x" for i in range(4)}))
    assert r.json()["error"]["code"] == "bad_archive"
    app.state.config.dataset_upload_max_mb = 1
    r = _upload(admin, ds, "big.zip", _zip({"r.bin": os.urandom(1024 * 1024 + 10)},
                                            compression=zipfile.ZIP_STORED))
    assert r.status_code in (413, 422)  # the upload itself or the unpacked size is over the limit
    assert _items(admin, ds) == {}


def test_a_plain_gzip_is_stored_as_a_file(admin, ds):
    r = _upload(admin, ds, "readings.json.gz", gzip.compress(b'{"a": 1}'))
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["media_type"] == "application/gzip" and item["tier"] == "large"
