"""docs/vocabulary.jsonld: the provisional LTMS ↔ I2IDL Glossary crosswalk (issue #140).

Offline checks only: no network and no JSON-LD library.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.xapi.translate import VERBS

VOCAB = Path(__file__).resolve().parents[2] / "docs" / "vocabulary.jsonld"
MATCHES = ("exactMatch", "closeMatch", "broadMatch", "relatedMatch")
GLOSSARY = "https://id.i2idl.org/concepts/"


@pytest.fixture(scope="module")
def doc():
    return json.loads(VOCAB.read_text(encoding="utf-8"))


def concepts(doc):
    return [n for n in doc["@graph"] if n.get("@type") == "skos:Concept"]


def test_parses_with_context_and_graph(doc):
    assert "@context" in doc
    assert isinstance(doc["@graph"], list) and doc["@graph"]


def test_scheme_is_marked_provisional(doc):
    schemes = [n for n in doc["@graph"] if n.get("@type") == "skos:ConceptScheme"]
    assert len(schemes) == 1
    assert "provisional" in schemes[0]["versionInfo"]


def test_every_record_type_has_a_concept(doc):
    ids = {n["@id"] for n in concepts(doc)}
    for record_type in VERBS:
        assert any(i.endswith(f"/{record_type}") for i in ids), record_type


def test_matches_are_lists_of_glossary_iris(doc):
    for node in concepts(doc):
        for rel in MATCHES:
            if rel in node:
                values = node[rel]
                assert isinstance(values, list) and values, (node["@id"], rel)
                for v in values:
                    assert isinstance(v, str) and v.startswith(GLOSSARY), (node["@id"], rel, v)


def test_every_concept_has_label_and_definition(doc):
    for node in concepts(doc):
        assert node.get("prefLabel"), node["@id"]
        assert node.get("definition"), node["@id"]


def test_ids_are_unique(doc):
    ids = [n["@id"] for n in doc["@graph"]]
    assert len(ids) == len(set(ids))
