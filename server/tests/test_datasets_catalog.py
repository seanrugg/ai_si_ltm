"""The fleet dataset catalog (issue #236)."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from server.models import AuditLog, Dataset

BODY = {"name": "Drive manuals", "description": "Wheel and motor docs", "kind": "upload",
        "licence": "Internal reference use"}


def _create(admin, **over):
    r = admin.post("/api/admin/datasets", json={**BODY, **over})
    return r


def test_create_approve_and_versions(admin, db_exec):
    r = _create(admin)
    assert r.status_code == 201, r.text
    ds = r.json()
    assert (ds["status"], ds["scope"], ds["use"], ds["current_version"]) == ("pending", "fleet", "reference", None)
    assert [v["status"] for v in ds["versions"]] == ["pending"]
    did = ds["id"]

    ds = admin.post(f"/api/admin/datasets/{did}/versions/1/approve").json()
    assert ds["status"] == "approved" and ds["current_version"] == 1 and ds["approved_by"].startswith("admin:")

    v2 = admin.post(f"/api/admin/datasets/{did}/versions")
    assert v2.status_code == 201 and v2.json() == {**v2.json(), "version": 2, "status": "pending"}
    assert admin.get(f"/api/admin/datasets/{did}").json()["current_version"] == 1  # v2 still quarantined
    assert admin.post(f"/api/admin/datasets/{did}/versions/2/approve").json()["current_version"] == 2

    listed = admin.get("/api/admin/datasets").json()["datasets"]
    assert [d["name"] for d in listed] == ["Drive manuals"] and len(listed[0]["versions"]) == 2

    async def actions(session):
        return [a.action for a in await session.scalars(
            select(AuditLog).where(AuditLog.action.like("dataset_%")).order_by(AuditLog.id))]
    assert db_exec(actions) == ["dataset_create", "dataset_approve", "dataset_version", "dataset_approve"]


def test_retire(admin):
    did = _create(admin).json()["id"]
    ds = admin.post(f"/api/admin/datasets/{did}/retire").json()
    assert ds["status"] == "retired"
    assert admin.post(f"/api/admin/datasets/{did}/versions").status_code == 409
    assert admin.post(f"/api/admin/datasets/{did}/versions/1/approve").status_code == 409


def test_validation_and_errors(admin, http):
    assert _create(admin).status_code == 201
    assert _create(admin).json()["error"]["code"] == "name_taken"
    assert _create(admin, name="x", licence="").json()["error"]["code"] == "bad_licence"
    assert _create(admin, name="y", kind="torrent").json()["error"]["code"] == "bad_kind"
    assert _create(admin, name="").json()["error"]["code"] == "bad_name"
    assert admin.get("/api/admin/datasets/not-a-uuid").status_code == 404
    assert admin.get("/api/admin/datasets/00000000-0000-0000-0000-000000000000").status_code == 404
    did = admin.get("/api/admin/datasets").json()["datasets"][0]["id"]
    assert admin.post(f"/api/admin/datasets/{did}/versions/9/approve").status_code == 404
    assert http.get("/api/admin/datasets", headers={"x-ltms-admin": "1"}).status_code == 401


def test_scope_other_than_fleet_is_refused_by_the_database(db_exec):
    async def go(session):
        session.add(Dataset(name="z", kind="upload", scope="swarm", licence="x", status="pending", added_by="t"))
        await session.flush()
    with pytest.raises(IntegrityError):
        db_exec(go)
