"""Tests for server.xapi.translate.record_to_statement.

Covers the xapi_golden.json cases, the xAPI statement shape, id
determinism/sensitivity, and error handling.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from server.xapi.translate import GLOSSARY, RECORD_CONCEPTS, record_to_statement

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_cases() -> list[dict]:
    with open(DATA_DIR / "xapi_golden.json", encoding="utf-8") as fh:
        return json.load(fh)


CASES = _load_cases()


def _payload_for(case: dict):
    payload = case.get("payload")
    if isinstance(payload, dict) and payload.get("$generate"):
        return {
            "kind": "sensor",
            "sensor": "battery",
            "unit": "percent",
            "samples": [{"t": i, "v": 70} for i in range(2000)],
        }
    return payload


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_golden(case: dict):
    assert (
        record_to_statement(case["record"], payload=_payload_for(case), **case["options"])
        == case["statement"]
    )


def test_statement_shape():
    for case in CASES:
        stmt = record_to_statement(
            case["record"], payload=_payload_for(case), **case["options"]
        )
        uuid.UUID(stmt["id"])  # must parse as a UUID
        assert stmt["actor"]["objectType"] == "Agent"
        assert stmt["actor"]["account"]["name"]
        assert stmt["object"]["id"].startswith("https://")
        assert "timestamp" in stmt
        assert "version" in stmt
        for key in stmt["context"]["extensions"]:
            assert key.startswith("https://")


def test_id_is_deterministic_and_hash_sensitive():
    rec = CASES[0]["record"]
    opts = CASES[0]["options"]
    first = record_to_statement(rec, payload=None, **opts)
    second = record_to_statement(rec, payload=None, **opts)
    assert first["id"] == second["id"]
    altered = dict(rec, hash="0" * 64)
    assert record_to_statement(altered, payload=None, **opts)["id"] != first["id"]


def test_invalid_type_and_version_raise():
    base = "https://ltms.example.org"
    rec = CASES[0]["record"]
    with pytest.raises(ValueError):
        record_to_statement(dict(rec, type="bogus"), payload=None, base=base)
    with pytest.raises(ValueError):
        record_to_statement(rec, payload=None, base=base, xapi_version="0.9")


def test_perception_without_telemetry_has_no_result():
    rec = {
        "agent": "robot-1",
        "ts": "2026-10-31T14:02:11Z",
        "prev_hash": "0" * 64,
        "payload_hash": "0" * 64,
        "sig": "0" * 128,
        "received_at": "2026-10-31T14:02:11.412000+00:00",
        "agent_kind": "robot",
        "seq": 1,
        "type": "perception",
        "hash": "0" * 64,
        "summary": "Obstacle 12 cm ahead",
    }
    stmt = record_to_statement(
        rec, payload={"kind": "pose", "samples": []}, base="https://ltms.example.org"
    )
    assert "result" not in stmt


def test_namespace_matches_profile_iri():
    import uuid
    from server.xapi import translate
    assert translate.NAMESPACE == uuid.uuid5(uuid.NAMESPACE_URL, translate.PROFILE_VERSION + "/holocron-mapping/1")


def test_place_with_spaces_is_percent_encoded():
    from server.xapi.translate import record_to_statement
    rec = {"agent": "robot-1", "seq": 1, "ts": "2026-10-31T14:00:00Z", "type": "event", "hash": "1" * 64,
           "prev_hash": "0" * 64, "payload_hash": "2" * 64, "sig": "3" * 128,
           "received_at": "2026-10-31T14:00:01+00:00", "agent_kind": "robot", "place": "kitchen table"}
    s = record_to_statement(rec, base="https://ltms.example.org")
    place = s["context"]["contextActivities"]["grouping"][0]
    assert place["id"] == "https://ltms.example.org/places/kitchen%20table"
    assert place["definition"]["name"] == {"en": "kitchen table"}


# ---- I2IDL Glossary concepts and swarm contextGroups (issue #142) ----------


BASE_CASE = next(c for c in CASES if c["record"].get("swarm"))


def _stmt(_version="2.0.0", **changes):
    rec = {**BASE_CASE["record"], **changes}
    return record_to_statement(rec, base=BASE_CASE["options"]["base"], xapi_version=_version)


def test_category_has_profile_then_learning_record_then_type_concept():
    decision = _stmt(type="decision")["context"]["contextActivities"]["category"]
    assert [c["id"] for c in decision][1:] == [GLOSSARY + "learning-record"]
    assert len(decision) == 2
    lesson = _stmt(type="lesson")["context"]["contextActivities"]["category"]
    assert len(lesson) == 3
    assert lesson[2]["id"] == GLOSSARY + "experiential-learning"
    assert lesson[2]["definition"]["type"] == "http://www.w3.org/2004/02/skos/core#Concept"
    assert decision[0]["definition"]["type"] == "http://adlnet.gov/expapi/activities/profile"


def test_category_applies_to_both_xapi_versions():
    old = _stmt(type="lesson", _version="1.0.3")["context"]["contextActivities"]["category"]
    assert [c["id"] for c in old][1:] == [GLOSSARY + "learning-record", GLOSSARY + "experiential-learning"]


def test_context_groups_only_for_swarm_records_at_2_0_0():
    with_swarm = _stmt()["context"]
    group = with_swarm["contextGroups"][0]
    assert group["objectType"] == "contextGroup"
    assert group["group"]["name"] == BASE_CASE["record"]["swarm"]
    assert group["relevantTypes"] == ["https://w3id.org/xapi-ai/activity-type/swarm"]
    assert "grouping" in with_swarm["contextActivities"]  # existing swarm grouping kept
    assert "contextGroups" not in _stmt(_version="1.0.3")["context"]
    assert "contextGroups" not in _stmt(swarm=None)["context"]


def test_glossary_targets_are_in_the_published_vocabulary():
    vocab = (Path(__file__).resolve().parents[2] / "docs" / "vocabulary.jsonld").read_text(encoding="utf-8")
    for concept in ("learning-record", *RECORD_CONCEPTS.values()):
        assert f'"{GLOSSARY}{concept}"' in vocab, concept
