"""ltms_mcp plain tool functions, tested with no MCP transport.

Copies the shape of ``test_recall.py`` and uses the ``mesh`` fixture:
``mesh.member("agent-x")`` returns an enrolled, approved ``LTMSClient`` against
the test app. The plain functions are imported from ``ltms_mcp``; an optional
test that imports ``server.py`` is guarded by ``pytest.importorskip("mcp")`` so
the suite still runs when the ``mcp`` package is not installed.
"""

from __future__ import annotations

import json

import pytest

from cli_agent.client import LTMSError
from ltms_contract import verify_record

from ltms_mcp import (
    MAX_PAYLOAD_BYTES,
    MAX_SUMMARY,
    MCPError,
    remember,
    recall_context,
    search_records,
)


@pytest.fixture
def agent(mesh):
    return mesh.member("agent-x", kind="agent", permissions=["contribute", "recall"])


def test_remember_writes_a_record_the_server_accepts(agent):
    agent.sync_head()
    out = remember(agent, "thought", summary="Vector may be asleep; check the dock first")
    assert set(out) == {"seq", "hash"}
    assert out["seq"] == 1


def test_remember_chain_continues_across_two_calls(agent):
    agent.sync_head()
    r1 = remember(agent, "thought", summary="First thought")
    r2 = remember(agent, "action", summary="Drove forward 0.5 m", place="hallway")
    assert r2["seq"] == r1["seq"] + 1
    recs = agent.records(agent="agent-x", limit=200)
    assert sorted(r["seq"] for r in recs) == [1, 2]
    for rec in recs:
        verify_record(rec, bytes(agent.key.verify_key))


def test_policy_and_system_are_refused(agent):
    agent.sync_head()
    with pytest.raises(MCPError) as exc:
        remember(agent, "policy", summary="No driving after 9pm")
    assert exc.value.code == "refused"
    with pytest.raises(MCPError) as exc:
        remember(agent, "system", summary="boot")
    assert exc.value.code == "refused"
    # Nothing was written.
    assert agent.records(agent="agent-x") == []


def test_oversized_summary_is_refused(agent):
    agent.sync_head()
    with pytest.raises(MCPError) as exc:
        remember(agent, "thought", summary="x" * (MAX_SUMMARY + 1))
    assert exc.value.code == "refused"


def test_oversized_payload_is_refused(agent):
    agent.sync_head()
    with pytest.raises(MCPError) as exc:
        remember(agent, "thought", summary="ok", payload={"data": "x" * (MAX_PAYLOAD_BYTES + 1)})
    assert exc.value.code == "refused"


def test_search_records_and_recall_context(agent):
    agent.sync_head()
    remember(agent, "lesson", summary="Slow down near Vector", place="living-room", peers=["vector"])
    remember(agent, "action", summary="Drove forward", place="hallway")

    found = search_records(agent, type="lesson")
    assert [r["summary"] for r in found] == ["Slow down near Vector"]

    found = search_records(agent, place="hallway")
    assert len(found) == 1 and found[0]["type"] == "action"

    found = search_records(agent, limit=1)
    assert len(found) == 1

    pack = recall_context(agent, intent="approach", target="vector", place="living-room")
    assert set(pack) >= {"outcomes", "comms", "lessons", "policies", "count"}


def test_scoped_member_sees_only_its_scope(mesh, agent):
    agent.sync_head()
    remember(agent, "lesson", summary="L1")
    remember(agent, "action", summary="A1")

    guest = mesh.member(
        "cloud-agent", kind="agent", permissions=["recall:scoped"],
        scope={"types": ["lesson", "policy"]},
    )
    seen = search_records(guest)
    assert {r["type"] for r in seen} <= {"lesson", "policy"}
    assert search_records(guest, type="action") == []


def test_outputs_never_contain_the_private_key(agent):
    agent.sync_head()
    out = remember(agent, "thought", summary="hello", peers=["vector"])
    pack = recall_context(agent)
    found = search_records(agent, type="thought")
    blob = json.dumps({"out": out, "pack": pack, "found": found}, default=str)
    assert bytes(agent.key).hex() not in blob


def test_ltms_error_carries_code_and_message(mesh):
    writer = mesh.member("writer", permissions=["contribute"])
    with pytest.raises(LTMSError) as exc:
        recall_context(writer)
    assert exc.value.code
    assert exc.value.message


def test_mcp_server_importable_only_when_installed():
    pytest.importorskip("mcp")
    from ltms_mcp import server

    assert server.mcp is not None
