"""Tests for server.xapi.translate.record_to_statement.

Covers the xapi_golden.json cases, the xAPI statement shape, id
determinism/sensitivity, and error handling.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from server.xapi.translate import GLOSSARY, RECORD_CONCEPTS, read_to_statement, record_to_statement

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_cases() -> list[dict]:
    with open(DATA_DIR / "xapi_golden.json", encoding="utf-8") as fh:
        return json.load(fh)


ALL_CASES = _load_cases()
# Record cases carry "record"; dataset-read cases (#239) carry "read".
CASES = [c for c in ALL_CASES if "record" in c]
READ_CASES = [c for c in ALL_CASES if "read" in c]


def _payload_for(case: dict):
    return case.get("payload")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_golden(case: dict):
    assert (
        record_to_statement(case["record"], payload=_payload_for(case), **case["options"])
        == case["statement"]
    )


@pytest.mark.parametrize("case", READ_CASES, ids=lambda c: c["name"])
def test_golden_reads(case: dict):
    assert read_to_statement(case["read"], case["dataset"], **case["options"]) == case["statement"]


def test_read_statement_id_and_optional_fields():
    case = READ_CASES[0]
    read = dict(case["read"], cycle=None, sha256=None, source="reported")
    stmt = read_to_statement(read, case["dataset"], **case["options"])
    assert stmt["id"] == case["statement"]["id"]  # the id depends only on the read id
    assert "registration" not in stmt["context"]
    ext = stmt["context"]["extensions"]
    assert not any(k.endswith("/resource-hash") for k in ext)
    assert ext["https://w3id.org/xapi-ai/extensions/read-source"] == "reported"
    with pytest.raises(ValueError):
        read_to_statement(dict(read, source="guessed"), case["dataset"], **case["options"])


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
    payload = {"kind": "pose", "samples": []}
    stmt = record_to_statement(
        rec, payload=payload, base="https://ltms.example.org"
    )
    assert "result" not in stmt
    # An untagged perception is an interpretation: "perceived", payload in
    # holocron/payload, no sensor concept (issue #179).
    assert stmt["verb"]["id"] == "https://w3id.org/xapi-ai/verbs/perceived"
    assert stmt["context"]["extensions"]["https://w3id.org/xapi-ai/extensions/holocron/payload"] == payload
    categories = [c["id"] for c in stmt["context"]["contextActivities"]["category"]]
    assert GLOSSARY + "sensor" not in categories
    assert GLOSSARY + "learning-record" in categories


def test_namespace_is_fixed_for_mapping_version_1():
    # Derived from the 0.10.0 IRI and fixed for mapping version 1, so
    # statement ids are unchanged even after PROFILE_VERSION moved to 0.10.1
    # (issue #179).
    from server.xapi import translate
    assert translate.NAMESPACE == uuid.uuid5(
        uuid.NAMESPACE_URL, "https://w3id.org/xapi-ai/profile/0.10.0/holocron-mapping/1"
    )


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
    # The profile IRI is the AIAAP version the mapping targets (issue #179).
    assert decision[0]["id"] == "https://w3id.org/xapi-ai/profile/0.10.1"
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


# ---- every payload is sent (issue #150) -------------------------------------

E = "https://w3id.org/xapi-ai/extensions/"


def _base_rec(**changes):
    rec = {**BASE_CASE["record"], **changes}
    rec.pop("outcome", None) if "outcome" not in changes else None
    return rec


def test_thought_decision_and_lesson_payloads_go_to_holocron_payload():
    for rtype in ("thought", "decision", "lesson"):
        p = {"text": f"{rtype} body", "n": 1}
        st = record_to_statement(_base_rec(type=rtype), base="https://x.example", payload=p)
        assert st["context"]["extensions"][E + "holocron/payload"] == p, rtype
        assert E + "holocron/payload-purged" not in st["context"]["extensions"]


def test_telemetry_over_16kb_is_sent_whole():
    p = {"kind": "sensor", "sensor": "battery", "unit": "percent",
         "samples": [{"t": i, "v": 70} for i in range(2000)]}
    assert len(json.dumps(p)) > 16 * 1024
    st = record_to_statement(_base_rec(type="perception", tags=["telemetry"]), base="https://x.example", payload=p)
    assert st["result"]["extensions"][E + "telemetry"] == p
    assert E + "holocron/payload" not in st["context"]["extensions"]


def test_telemetry_of_any_kind_goes_to_the_telemetry_extension():
    # Any sensor's data translates, whatever its kind (issue #179); v may be
    # a list.
    p = {"kind": "thermal-array", "sensor": "ir-blanket", "unit": "K",
         "samples": [{"t": 0.0, "v": [310.2, 309.8]}, {"t": 0.1, "v": [311.5, 310.1]}]}
    st = record_to_statement(_base_rec(type="perception", tags=["telemetry"]), base="https://x.example", payload=p)
    assert st["result"]["extensions"][E + "telemetry"] == p
    assert E + "holocron/payload" not in st["context"]["extensions"]
    assert st["verb"]["id"] == "https://w3id.org/xapi-ai/verbs/sensed"
    assert GLOSSARY + "sensor" in [c["id"] for c in st["context"]["contextActivities"]["category"]]


def test_missing_payload_is_marked_purged():
    st = record_to_statement(_base_rec(type="thought"), base="https://x.example", payload=None)
    assert st["context"]["extensions"][E + "holocron/payload-purged"] is True


def test_outcome_on_a_non_action_record_goes_to_result_extensions_only():
    st = record_to_statement(_base_rec(type="decision", outcome="success"), base="https://x.example", payload={})
    assert st["result"] == {"extensions": {E + "outcome": "success"}}
    action = record_to_statement(_base_rec(type="action", outcome="failure"), base="https://x.example", payload={})
    assert action["result"]["success"] is False


def test_header_raw_is_passed_through_as_the_exact_string():
    raw = '{"agent":"robot-1","seq":1}'
    st = record_to_statement(_base_rec(type="thought", header_raw=raw), base="https://x.example", payload={})
    assert st["context"]["extensions"][E + "holocron/header"] == raw


# --- cause and effect: StatementRef (issue #180) ---

PERCEPTION = {**BASE_CASE["record"], "type": "perception", "seq": 41, "hash": "a" * 64, "refs": None}


def _decision(refs, ref_hashes):
    rec = _base_rec(type="decision", seq=42, hash="b" * 64, refs=refs)
    return record_to_statement(rec, base="https://x.example", payload={"x": 1}, ref_hashes=ref_hashes)


def test_because_links_to_the_cause_statement():
    cause = record_to_statement(PERCEPTION, base="https://x.example", payload={"x": 1})
    for refs in ({"because": "robot-1:41"}, {"because": ["robot-1:41"]}):
        st = _decision(refs, {"robot-1:41": "a" * 64})
        assert st["context"]["statement"] == {"objectType": "StatementRef", "id": cause["id"]}
        assert st["context"]["extensions"][E + "holocron/refs"] == refs


def test_unknown_or_several_causes_get_no_statementref():
    hashes = {"robot-1:41": "a" * 64, "robot-1:40": "c" * 64}
    for refs in (
        {"because": "robot-1:99"},                    # not resolved
        {"because": ["robot-1:41", "robot-1:40"]},    # several causes
        {"derived_from": "robot-1:41"},               # not a cause
        {"because": 41},                              # not "agent:seq"
    ):
        st = _decision(refs, hashes)
        assert "statement" not in st["context"], refs
        assert st["context"]["extensions"][E + "holocron/refs"] == refs
    assert "statement" not in _decision({"because": "robot-1:41"}, None)["context"]


def test_statementref_does_not_change_the_statement_id():
    with_ref = _decision({"because": "robot-1:41"}, {"robot-1:41": "a" * 64})
    without = _decision({"because": "robot-1:41"}, None)
    assert with_ref["id"] == without["id"] == str(uuid.uuid5(uuid.UUID(
        "19413c7a-d33c-5129-b99b-07d709cef285"), "b" * 64))
