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


# ---------------------------------------------------------------- mapping v2 (#472, AIAAP 0.12.0)
from server.xapi.translate import NAMESPACE, harness_kind, iso_duration_ms  # noqa: E402

AI = "https://w3id.org/xapi-ai"
X = AI + "/extensions/"
V2_BASE = "https://ltms.example.org"


def _v2_rec(rtype, tags, seq=7, **extra):
    rec = {"agent": "agent-1", "ts": "2026-11-22T21:49:02.871Z", "prev_hash": "a" * 64, "payload_hash": "b" * 64,
           "sig": "c" * 128, "received_at": "2026-11-22T21:49:03.118000+00:00", "agent_kind": "agent",
           "seq": seq, "type": rtype, "hash": f"{seq:064d}", "tags": tags, "header_raw": '{"x":1}'}
    rec.update(extra)
    return rec


TOOL_PAYLOAD = {"tool_name": "mcp__vector__vector_say", "ok": True, "duration_ms": 1000, "session_id": "s1",
                "task_id": "t1", "model": "nemotron-3-nano-64k", "record_mode": "full",
                "args": {"text": "Hello"}, "result_raw": '{"status": "ok"}', "result_truncated": False}
ROLLUP_PAYLOAD = {"rollup_schema": 1, "date": "2026-11-21",
                  "sessions": {"started": 1, "completed": 1, "interrupted": 0},
                  "tool_calls": {"mcp__vector__vector_say": {"ok": 0, "error": 1}}, "tool_errors": {"timeout": 1},
                  "model_routes": {"custom/nemotron-3-nano-64k": {"calls": 1, "input_tokens": 900,
                                                                  "output_tokens": 80, "api_seconds": 3.2}},
                  "api_errors": {"network": 1}, "compactions": 0,
                  "machine": {"arch": "aarch64", "os": "linux", "ram_gb": 8, "hermes_version": "0.23.3",
                              "plugin_version": "0.2.0"}}


def _tool_stmt(payload=TOOL_PAYLOAD, **kw):
    rec = _v2_rec("action", ["hermes", "tool-call"], summary="mcp__vector__vector_say ok (1000 ms)",
                  outcome="success", peers=["vector-1"])
    return rec, record_to_statement(rec, base=V2_BASE, payload=payload, **kw)


def test_tool_call_becomes_invoked_tool():
    rec, st = _tool_stmt()
    assert st["id"] == str(uuid.uuid5(NAMESPACE, rec["hash"]))  # same id as v1
    assert st["verb"]["id"] == AI + "/verbs/invoked-tool"
    assert st["object"] == {"objectType": "Activity", "id": V2_BASE + "/tools/mcp__vector__vector_say",
                            "definition": {"type": AI + "/activity-type/tool",
                                           "name": {"en": "mcp__vector__vector_say"}}}
    ca = st["context"]["contextActivities"]
    assert ca["other"] == [{"objectType": "Activity", "id": V2_BASE + "/records/agent-1/7",
                            "definition": {"type": AI + "/activity-type/record",
                                           "name": {"en": "mcp__vector__vector_say ok (1000 ms)"}}}]
    assert ca["category"][0]["id"] == AI + "/profile/0.12.0"
    assert st["result"]["success"] is True
    assert st["result"]["duration"] == "PT1S"
    assert st["result"]["response"] == '{"status": "ok"}'
    rx = st["result"]["extensions"]
    assert rx[X + "latency-ms"] == 1000 and rx[X + "tool-result-truncated"] is False
    assert rx[X + "outcome"] == "success"
    ext = st["context"]["extensions"]
    assert ext[X + "tool-name"] == "mcp__vector__vector_say"
    assert ext[X + "tool-parameters"] == {"text": "Hello"}
    assert ext[X + "session-id"] == "s1" and ext[X + "model-id"] == "nemotron-3-nano-64k"
    assert ext[X + "harness"] == {"name": "hermes", "plugin": "ltms_recorder"}
    assert ext[X + "holocron/mapping-version"] == "2"
    for k in ("seq", "hash", "prev-hash", "payload-hash", "signature", "record-type", "received-at", "header",
              "payload"):
        assert X + "holocron/" + k in ext
    assert ext[X + "holocron/payload"] == TOOL_PAYLOAD  # the signed payload still verifies
    assert st["context"]["contextAgents"][0]["agent"]["account"]["name"] == "vector-1"


def test_summary_mode_tool_call_has_no_parameters_or_response():
    payload = {k: v for k, v in TOOL_PAYLOAD.items() if k not in ("args", "result_raw", "result_truncated")}
    _, st = _tool_stmt(payload=dict(payload, record_mode="summary", duration_ms=1250, ok=False))
    assert st["verb"]["id"] == AI + "/verbs/invoked-tool"
    assert "response" not in st["result"] and st["result"]["duration"] == "PT1.25S"
    assert X + "tool-parameters" not in st["context"]["extensions"]
    assert X + "tool-result-truncated" not in st["result"]["extensions"]


def test_truncation_flags():
    _, st = _tool_stmt(payload=dict(TOOL_PAYLOAD, args="{\"text\": \"Hel", args_truncated=True,
                                     result_truncated=True))
    assert st["context"]["extensions"][X + "tool-parameters-truncated"] is True
    assert st["result"]["extensions"][X + "tool-result-truncated"] is True


def test_purged_tool_call_falls_back_to_v1():
    rec, st = _tool_stmt(payload=None)
    assert st["verb"]["id"] == AI + "/verbs/performed"
    assert st["context"]["extensions"][X + "holocron/mapping-version"] == "1"
    assert st["context"]["extensions"][X + "holocron/payload-purged"] is True


def test_agent_rollup_becomes_reported_usage_not_sensed():
    rec = _v2_rec("perception", ["telemetry", "internal", "agent-rollup"], seq=9,
                  summary="daily rollup 2026-11-21: 1 sessions, 1 tool calls, 1 errors")
    st = record_to_statement(rec, base=V2_BASE, payload=ROLLUP_PAYLOAD)
    assert st["verb"]["id"] == AI + "/verbs/reported-usage"
    assert st["object"]["id"] == V2_BASE + "/usage/agent-1/2026-11-21"
    assert st["object"]["definition"]["type"] == AI + "/activity-type/usage-report"
    usage = st["result"]["extensions"][X + "usage"]
    assert usage["usage_schema"] == 1 and "date" not in usage and "rollup_schema" not in usage
    assert usage["machine"]["harness_version"] == "0.23.3" and "hermes_version" not in usage["machine"]
    assert usage["tool_errors"] == {"timeout": 1}
    assert X + "telemetry" not in st["result"]["extensions"]
    assert "response" not in st["result"]
    ext = st["context"]["extensions"]
    assert ext[X + "usage-period"] == "2026-11-21"
    assert ext[X + "harness"] == {"name": "hermes", "plugin": "ltms_recorder", "version": "0.2.0"}
    assert ext[X + "holocron/payload"] == ROLLUP_PAYLOAD
    cats = [c["id"] for c in st["context"]["contextActivities"]["category"]]
    assert cats[0] == AI + "/profile/0.12.0" and GLOSSARY + "sensor" not in cats


def test_other_telemetry_is_still_sensed_v1():
    rec = _v2_rec("perception", ["telemetry", "internal"])
    st = record_to_statement(rec, base=V2_BASE, payload={"kind": "health", "cpu": 3})
    assert st["verb"]["id"] == AI + "/verbs/sensed"
    assert st["context"]["extensions"][X + "holocron/mapping-version"] == "1"


def test_harness_kind_and_duration_helpers():
    assert harness_kind({"type": "action", "tags": ["tool-call"]}, {"tool_name": ""}) is None
    assert harness_kind({"type": "decision", "tags": ["tool-call"]}, TOOL_PAYLOAD) is None
    assert harness_kind({"type": "perception", "tags": ["agent-rollup"]}, {"no": "date"}) is None
    assert iso_duration_ms(0) == "PT0S" and iso_duration_ms(12) == "PT0.012S"
    assert iso_duration_ms(None) is None and iso_duration_ms(True) is None and iso_duration_ms(-1) is None


def test_v2_statements_use_only_aiaap_0_12_0_terms():
    """Every w3id IRI in a v2 statement is defined in the published AIAAP profile (when available)."""
    import re
    profile_path = Path(__file__).resolve().parents[3] / "xapi-ai" / "ai_agent_actions_profile.json"
    if not profile_path.exists():
        pytest.skip("xapi-ai checkout not next to this repo")
    profile = json.loads(profile_path.read_text())
    defined = {c["id"] for c in profile["concepts"]} | {v["id"] for v in profile["versions"]}
    rec = _v2_rec("perception", ["telemetry", "internal", "agent-rollup"], seq=9)
    for st in (_tool_stmt()[1], record_to_statement(rec, base=V2_BASE, payload=ROLLUP_PAYLOAD)):
        used = set(re.findall(r"https://w3id\.org/xapi-ai/[^\"']+", json.dumps(st)))
        assert used <= defined, used - defined
