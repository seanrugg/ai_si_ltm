"""Tests for server.xapi.translate.record_to_statement.

Covers the xapi_golden.json cases, the xAPI statement shape, id
determinism/sensitivity, and error handling.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from server.xapi.translate import record_to_statement

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
