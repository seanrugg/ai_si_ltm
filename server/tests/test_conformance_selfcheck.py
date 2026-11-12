"""The conformance suite passes against this repo's own harness (#298)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance"))

from adapter import CliAgentAdapter  # noqa: E402
from cases import CASES, run  # noqa: E402
from ltms_contract import generate_key, save_key  # noqa: E402


@pytest.fixture
def harness(http, admin, tmp_path):
    from cli_agent.client import LTMSClient
    key_path = tmp_path / "conf.json"
    save_key(key_path, "conf-member", generate_key())
    c = LTMSClient(None, key_path=str(key_path), http=http)
    mid = c.enroll("robot", ["contribute", "recall"])["id"]
    assert admin.post(f"/api/admin/members/{mid}/approve", json={}).status_code == 200
    return CliAgentAdapter(None, str(key_path), str(tmp_path / "state"), http=http)


def test_reference_harness_passes_every_case(harness):
    results = run(harness)
    assert [r for r in results if not r[1]] == [], results
    assert len(results) == len(CASES)


def test_a_broken_harness_fails_the_right_case(harness):
    """A harness that forgets its chain head on restart is caught."""
    orig = harness.restart

    def amnesiac_restart():
        orig()
        harness.link._head = {"seq": 0, "hash": "0" * 64}
    harness.restart = amnesiac_restart
    results = dict((n, ok) for n, ok, _ in run(harness))
    assert results["chain_continues_across_restart"] is False
