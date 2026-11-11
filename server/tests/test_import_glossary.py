"""Importing the I2IDL glossary as a fleet dataset (issue #253). No network."""
from __future__ import annotations

import json

import pytest

from scripts import import_glossary as ig
from server.tests.test_datasets_read import _get, data_dir  # noqa: F401

SRC = "https://id.i2idl.org/sources/"


def doc(version="v0.0.93", extra=()):
    graph = [
        {"@id": "https://id.i2idl.org/scheme", "@type": ["skos:ConceptScheme", "schema:DefinedTermSet"],
         "dcterms:title": "I2IDL Glossary of Digital Learning", "gs:publicationVersion": version,
         "dcterms:rights": "Mixed rights. I2IDL-original material defaults to CC BY 4.0; source-specific rights "
                           "are preserved at the source and evidence level."},
        {"@id": "https://id.i2idl.org/collections/field/data", "@type": "skos:Collection",
         "skos:prefLabel": "Data & Instrumentation"},
        {"@id": "https://id.i2idl.org/collections/type/concept", "@type": "skos:Collection", "skos:prefLabel": "Concept"},
        {"@id": SRC + "xapi", "@type": "dcterms:BibliographicResource", "dcterms:title": "IEEE xAPI Base Standard",
         "dcterms:rights": "Apache 2.0", "gs:rightsUrl": "https://www.apache.org/licenses/LICENSE-2.0"},
        {"@id": "https://id.i2idl.org/definitions/learning-record-1", "@type": ["prov:Entity", "gs:Definition"],
         "rdf:value": "A machine-readable record of a learning event.",
         "gs:evidence": [{"@type": "gs:Evidence", "dcterms:source": {"@id": SRC + "xapi"},
                          "gs:sourceLabel": "IEEE xAPI Base Standard", "gs:citationDetail": "§3.1",
                          "gs:evidenceRelation": "direct", "schema:url": "https://example.org/xapi#31"}]},
        {"@id": "https://id.i2idl.org/concepts/learning-record", "@type": ["skos:Concept", "schema:DefinedTerm"],
         "dcterms:identifier": "learning-record", "skos:prefLabel": "Learning record",
         "skos:definition": "A machine-readable record of a learning event.",
         "gs:activeDefinition": {"@id": "https://id.i2idl.org/definitions/learning-record-1"},
         "gs:fieldCollection": {"@id": "https://id.i2idl.org/collections/field/data"},
         "gs:typeCollection": {"@id": "https://id.i2idl.org/collections/type/concept"},
         "skos:altLabel": "LR",
         "skos:related": [{"@id": "https://id.i2idl.org/concepts/xapi-statement"}]},
        {"@id": "https://id.i2idl.org/concepts/xapi-statement", "@type": ["skos:Concept", "schema:DefinedTerm"],
         "dcterms:identifier": "xapi-statement", "skos:prefLabel": "xAPI statement",
         "skos:definition": "An actor-verb-object record of an experience."},
        *extra,
    ]
    return {"@context": {}, "@graph": graph}


def test_build_one_item_per_concept_with_rights():
    pub, items = ig.build(doc())
    assert pub["publicationVersion"] == "v0.0.93" and "CC BY 4.0" in pub["rights"]
    assert [i["id"] for i in items] == ["learning-record", "xapi-statement"]
    lr = items[0]
    assert lr["term"] == "Learning record" and lr["aka"] == ["LR"]
    assert lr["field"] == "Data & Instrumentation" and lr["type"] == "Concept"
    assert lr["references"] == [{"source": "IEEE xAPI Base Standard", "title": "IEEE xAPI Base Standard",
                                 "detail": "§3.1", "url": "https://example.org/xapi#31", "relation": "direct",
                                 "rights": "Apache 2.0", "rights_url": "https://www.apache.org/licenses/LICENSE-2.0"}]
    assert lr["relationships"] == {"related": ["https://id.i2idl.org/concepts/xapi-statement"]}
    assert lr["attribution"].endswith("(v0.0.93), https://id.i2idl.org/concepts/learning-record")
    with pytest.raises(ValueError):
        ig.build({"@graph": []})


def test_import_then_rerun_is_a_noop_then_a_new_publication(admin, mesh):
    out = ig.import_glossary(admin, doc(), log=lambda *_: None)
    assert out == {"status": "imported", "version": 1, "publicationVersion": "v0.0.93", "items": 2}
    [ds] = admin.get("/api/admin/datasets").json()["datasets"]
    assert ds["name"] == "i2idl-glossary" and ds["kind"] == "url" and "CC BY 4.0" in ds["licence"]
    keys = [i["key"] for i in admin.get(f"/api/admin/datasets/{ds['id']}/versions/1/items").json()["items"]]
    assert sorted(keys) == ["concepts/learning-record.json", "concepts/xapi-statement.json",
                            "publication/v0.0.93.json"]
    assert ds["versions"][0]["status"] == "pending"  # an admin approves it

    again = ig.import_glossary(admin, doc(), log=lambda *_: None)
    assert again["status"] == "unchanged" and again["version"] == 1

    admin.post(f"/api/admin/datasets/{ds['id']}/versions/1/approve")
    # Members can now search by term and definition and read the concept.
    z3 = mesh.member("z3-r0")
    hits = _get(z3, f"/api/datasets/{ds['id']}/search", q="machine-readable record").json()["hits"]
    assert hits[0]["key"] == "concepts/learning-record.json"
    item = _get(z3, f"/api/datasets/{ds['id']}/items/concepts/learning-record.json").json()
    assert json.loads(item["text"])["uri"] == "https://id.i2idl.org/concepts/learning-record"

    newer = ig.import_glossary(admin, doc("v0.0.94"), log=lambda *_: None)
    assert newer["status"] == "imported" and newer["version"] == 2
    assert ig.import_glossary(admin, doc("v0.0.93"), log=lambda *_: None)["status"] == "unchanged"


def test_from_file_needs_no_network(tmp_path, monkeypatch):
    path = tmp_path / "glossary.jsonld"
    path.write_text(json.dumps(doc()))
    seen = {}

    def no_network(*a, **k):
        raise AssertionError("fetched over the network")

    class FakeClient:
        def __init__(self, **kw):
            seen["base_url"] = kw["base_url"]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            seen.setdefault("posts", []).append(url)
            return type("R", (), {"status_code": 200, "json": lambda self: {}, "text": ""})()

    monkeypatch.setattr(ig.httpx, "get", no_network)
    monkeypatch.setattr(ig.httpx, "Client", FakeClient)
    monkeypatch.setattr(ig, "import_glossary", lambda admin, d: seen.setdefault("doc", d))
    monkeypatch.setenv("LTMS_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("LTMS_ADMIN_PASSWORD", "pw")
    assert ig.main(["--server", "https://ltms.example.org/", "--from-file", str(path)]) == 0
    assert seen["doc"]["@graph"][0]["gs:publicationVersion"] == "v0.0.93"
    assert seen["base_url"] == "https://ltms.example.org" and seen["posts"] == ["/api/auth/admin/login"]
