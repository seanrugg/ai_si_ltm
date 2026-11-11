"""Catalog integrations and reported reads (issue #248)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from server.models import CatalogIntegration, DatasetRead
from server.tests.test_datasets_read import _get, data_dir  # noqa: F401


def _integration(admin, **kw):
    body = {"name": "hf", "kind": "huggingface", "base_url": "https://huggingface.co", "credential_env": "HF_TOKEN"}
    body.update(kw)
    return admin.post("/api/admin/integrations", json=body)


def _external_dataset(admin, integration="hf", items=None, approve=True):
    r = admin.post("/api/admin/datasets", json={"name": "Robot demos", "kind": "integration",
                                                "licence": "CC BY 4.0", "integration": integration})
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    items = items or [{"key": "demos/pick-place", "locator": "lerobot/pick_place", "media_type": "application/x-parquet",
                       "size_bytes": 123456}]
    r = admin.post(f"/api/admin/datasets/{did}/versions/1/external", json={"items": items})
    assert r.status_code == 200, r.text
    if approve:
        assert admin.post(f"/api/admin/datasets/{did}/versions/1/approve").status_code == 200
    return did


def test_create_list_delete(admin):
    r = _integration(admin, description="Public robot datasets")
    assert r.status_code == 201, r.text
    assert r.json()["credential_env"] == "HF_TOKEN"
    assert [i["name"] for i in admin.get("/api/admin/integrations").json()["integrations"]] == ["hf"]
    assert _integration(admin).json()["error"]["code"] == "name_taken"
    _external_dataset(admin)
    assert admin.delete("/api/admin/integrations/hf").json()["error"]["code"] == "in_use"
    assert _integration(admin, name="spare", kind="http", base_url="https://data.example.org").status_code == 201
    assert admin.delete("/api/admin/integrations/spare").status_code == 200


@pytest.mark.parametrize("kw,code", [
    ({"kind": "ftp"}, "bad_kind"),
    ({"base_url": "file:///etc"}, "bad_url"),
    ({"base_url": "https://user:pw@huggingface.co"}, "credential_in_url"),
    ({"base_url": "https://huggingface.co/?token=abc"}, "credential_in_url"),
    ({"credential_env": "hf_abcdefghijklmnop"}, "bad_credential_env"),
    ({"credential_env": "sk-live-123"}, "bad_credential_env"),
    ({"credential_env": "not a name"}, "bad_credential_env"),
    ({"credential_env": "AKIAIOSFODNN7EXAMPLE"}, "bad_credential_env"),
])
def test_no_field_stores_a_credential(admin, kw, code):
    r = _integration(admin, **kw)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_the_model_has_no_secret_column():
    cols = {c.name for c in CatalogIntegration.__table__.columns}
    assert not cols & {"secret", "secret_enc", "token", "password", "api_key"}


def test_members_see_the_locator_and_report_reads(admin, mesh, db_exec):
    _integration(admin)
    did = _external_dataset(admin)
    z3 = mesh.member("z3-r0")
    [ds] = _get(z3, "/api/datasets").json()["datasets"]
    assert ds["kind"] == "integration"
    assert ds["integration"] == {"name": "hf", "kind": "huggingface", "base_url": "https://huggingface.co",
                                 "credential_env": "HF_TOKEN"}
    item = _get(z3, f"/api/datasets/{did}/items/demos/pick-place").json()
    assert item["tier"] == "external" and item["locator"] == "lerobot/pick_place"
    assert item["integration"]["kind"] == "huggingface" and item["text"] is None
    assert db_exec(lambda s: s.scalar(select(DatasetRead.id))) is None  # fetching the locator isn't a read

    cycle = "5b0f6a3e-2c1d-4e8f-9a7b-1c2d3e4f5a6b"
    assert z3.report_read(did, 1, "demos/pick-place", cycle=cycle) == {"logged": True}
    assert z3.report_read(did, 1, "demos/pick-place", cycle=cycle) == {"logged": False}  # once per cycle

    async def reads(session):
        return [(r.item_key, r.source) for r in await session.scalars(select(DatasetRead))]
    assert db_exec(reads) == [("demos/pick-place", "reported")]


def test_unknown_or_unapproved_reports_are_404(admin, mesh):
    _integration(admin)
    did = _external_dataset(admin, approve=False)
    z3 = mesh.member("z3-r0")
    z3.login()
    for body in ({"dataset": did, "version": 1, "item": "demos/pick-place"},
                 {"dataset": "00000000-0000-0000-0000-000000000000", "version": 1, "item": "x"}):
        r = z3.http.post("/api/members/me/reads", json=body, headers={"Authorization": f"Bearer {z3.token}"})
        assert r.status_code == 404, r.text
    admin.post(f"/api/admin/datasets/{did}/versions/1/approve")
    r = z3.http.post("/api/members/me/reads", json={"dataset": did, "version": 1, "item": "nope"},
                     headers={"Authorization": f"Bearer {z3.token}"})
    assert r.status_code == 404
    r = z3.http.post("/api/members/me/reads", json={"dataset": did, "version": "1", "item": "nope"},
                     headers={"Authorization": f"Bearer {z3.token}"})
    assert r.status_code == 422


def test_model_integrations_need_a_model(admin):
    _integration(admin, name="lab-ollama", kind="ollama", base_url="http://ollama.local:11434", credential_env=None)
    r = admin.post("/api/admin/datasets", json={"name": "Models", "kind": "integration", "licence": "Apache-2.0",
                                                "integration": "lab-ollama"})
    did = r.json()["id"]
    r = admin.post(f"/api/admin/datasets/{did}/versions/1/external",
                   json={"items": [{"key": "qwen", "locator": "qwen3:8b"}]})
    assert r.json()["error"]["code"] == "bad_model"
    r = admin.post(f"/api/admin/datasets/{did}/versions/1/external",
                   json={"items": [{"key": "qwen", "locator": "http://ollama.local:11434", "model": "qwen3:8b"}]})
    assert r.status_code == 200 and r.json()["items"][0]["meta"] == {"locator": "http://ollama.local:11434",
                                                                       "model": "qwen3:8b"}
    assert admin.get(f"/api/admin/datasets/{did}").json()["use"] == "reference"


def test_mcp_integration_names_a_server(admin):
    r = _integration(admin, name="glossary", kind="mcp", base_url="https://glossary.example.org/mcp",
                     credential_env=None)
    assert r.status_code == 201 and r.json()["kind"] == "mcp"


def test_external_items_need_an_integration_dataset(admin):
    did = admin.post("/api/admin/datasets", json={"name": "Plain", "kind": "upload", "licence": "x"}).json()["id"]
    r = admin.post(f"/api/admin/datasets/{did}/versions/1/external", json={"items": [{"key": "a", "locator": "b"}]})
    assert r.json()["error"]["code"] == "not_integration"
    r = admin.post("/api/admin/datasets", json={"name": "NoSrc", "kind": "integration", "licence": "x"})
    assert r.json()["error"]["code"] == "bad_integration"
