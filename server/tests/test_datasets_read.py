"""Members read and search fleet datasets; every read is logged (issue #238)."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from server.models import DatasetRead

DATA = Path(__file__).parent / "data" / "datasets"


@pytest.fixture(autouse=True)
def data_dir(app, tmp_path):
    app.state.config.data_dir = str(tmp_path / "data")


def _dataset(admin, name="Manuals", files=("drive.md", "notes.txt"), approve=True):
    did = admin.post("/api/admin/datasets", json={"name": name, "kind": "upload", "licence": "Internal"}).json()["id"]
    admin.post(f"/api/admin/datasets/{did}/versions/1/items",
               files=[("files", (n, (DATA / n).read_bytes())) for n in files])
    if approve:
        admin.post(f"/api/admin/datasets/{did}/versions/1/approve")
    return did


def _get(member, path, **params):
    member.login()
    return member.http.get(path, params=params, headers={"Authorization": f"Bearer {member.token}"})


def _reads(db_exec):
    async def go(session):
        return [(r.item_key, r.version, r.cycle) for r in await session.scalars(
            select(DatasetRead).order_by(DatasetRead.id))]
    return db_exec(go)


def test_only_approved_datasets_are_visible(mesh, admin):
    z3 = mesh.member("z3-r0")
    did = _dataset(admin)
    pending = _dataset(admin, name="Draft", approve=False)
    listed = _get(z3, "/api/datasets").json()["datasets"]
    assert [d["name"] for d in listed] == ["Manuals"] and listed[0]["current_version"] == 1
    assert listed[0]["scope"] == "fleet"
    assert _get(z3, f"/api/datasets/{pending}/items/drive.md").status_code == 404
    admin.post(f"/api/admin/datasets/{did}/retire")
    assert _get(z3, "/api/datasets").json()["datasets"] == []
    assert _get(z3, f"/api/datasets/{did}/items/drive.md").status_code == 404


def test_search_finds_a_phrase(mesh, admin):
    z3 = mesh.member("z3-r0")
    did = _dataset(admin)
    r = _get(z3, f"/api/datasets/{did}/search", q="wheel stalls")
    assert r.status_code == 200, r.text
    hits = r.json()["hits"]
    assert [h["key"] for h in hits] == ["drive.md"] and "wheel" in hits[0]["snippet"].lower()
    assert _get(z3, f"/api/datasets/{did}/search", q="").json()["error"]["code"] == "bad_query"


def test_reads_are_logged_once_per_cycle(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    did = _dataset(admin)
    item = _get(z3, f"/api/datasets/{did}/items/drive.md").json()
    assert item["media_type"] == "text/markdown" and "left wheel stalls" in item["text"] and item["version"] == 1
    cycle = str(uuid.uuid4())
    _get(z3, f"/api/datasets/{did}/items/drive.md", cycle=cycle)
    _get(z3, f"/api/datasets/{did}/items/drive.md", cycle=cycle)  # same cycle: not logged again
    _get(z3, f"/api/datasets/{did}/items/drive.md", cycle=str(uuid.uuid4()))
    reads = _reads(db_exec)
    assert len(reads) == 3 and reads[0][2] is None and str(reads[1][2]) == cycle
    assert _get(z3, f"/api/datasets/{did}/items/drive.md", cycle="nope").json()["error"]["code"] == "bad_cycle"
    assert _get(z3, f"/api/datasets/{did}/items/missing.md").status_code == 404


def test_older_approved_versions_stay_readable(mesh, admin):
    z3 = mesh.member("z3-r0")
    did = _dataset(admin)
    admin.post(f"/api/admin/datasets/{did}/versions")
    admin.post(f"/api/admin/datasets/{did}/versions/2/items", files=[("files", ("robot.yaml", (DATA / "robot.yaml").read_bytes()))])
    assert _get(z3, f"/api/datasets/{did}/items/robot.yaml").status_code == 404  # v2 pending: current is v1
    admin.post(f"/api/admin/datasets/{did}/versions/2/approve")
    assert _get(z3, f"/api/datasets/{did}/items/robot.yaml").json()["version"] == 2
    assert _get(z3, f"/api/datasets/{did}/items/drive.md", version=1).json()["version"] == 1
    assert _get(z3, f"/api/datasets/{did}/items/drive.md").status_code == 404  # not in v2


def test_members_only(http, admin):
    did = _dataset(admin)
    assert http.get(f"/api/datasets/{did}/items/drive.md").status_code == 401
    assert http.get("/api/datasets").status_code == 401
