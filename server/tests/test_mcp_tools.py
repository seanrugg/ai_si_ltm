"""MCP tools: plain functions over an LTMSClient (no MCP transport needed).

Mirrors the shape of server/tests/test_recall.py and uses mesh.member("agent-x").
"""
from __future__ import annotations

import json

import httpx
import pytest

from ltms_contract import verify_record

from ltms_mcp import MAX_PAYLOAD_BYTES, ToolError, remember, recall_context, search_records


@pytest.fixture
def agent(mesh):
    return mesh.member("agent-x")


def test_remember_writes_and_chain_continues(agent):
    r1 = remember(agent, "thought", summary="First thought", payload={"x": 1})
    r2 = remember(agent, "decision", summary="Approach Vector", payload={"y": 2})

    assert r1["seq"] == 1
    assert r2["seq"] == 2
    recs = agent.records(limit=20)
    by_seq = {r["seq"]: r for r in recs}
    assert set(by_seq) == {1, 2}
    assert by_seq[2]["prev_hash"] == by_seq[1]["hash"]  # chain continues across calls
    for rec in recs:
        verify_record(rec, bytes(agent.key.verify_key))


def test_remember_refuses_forbidden_types(agent):
    with pytest.raises(ToolError) as exc:
        remember(agent, "policy", summary="No driving after 9pm")
    assert exc.value.code == "type_forbidden"
    with pytest.raises(ToolError) as exc:
        remember(agent, "system", summary="Reserved for the server")
    assert exc.value.code == "type_forbidden"


def test_remember_refuses_oversized(agent):
    with pytest.raises(ToolError) as exc:
        remember(agent, "thought", summary="x" * (280 + 1))
    assert exc.value.code == "summary_too_long"

    with pytest.raises(ToolError) as exc:
        remember(agent, "thought", summary="ok",
                 payload={"data": "x" * (MAX_PAYLOAD_BYTES + 1)})
    assert exc.value.code == "payload_too_large"


def test_search_records_and_recall_context(agent):
    remember(agent, "thought", summary="First thought", place="hallway")
    remember(agent, "decision", summary="Approach Vector", place="hallway",
             peers=["vector"])

    assert len(search_records(agent, type="thought")) == 1
    assert len(search_records(agent, place="hallway")) == 2
    assert len(search_records(agent, limit=1)) == 1

    agent.append("action", summary="Approach Vector", place="hallway",
                 peers=["vector"], outcome="success")
    agent.flush()
    pack = recall_context(agent, intent="approach", target="vector", place="hallway")
    assert set(pack) >= {"outcomes", "comms", "lessons", "policies", "count"}
    assert [r["summary"] for r in pack["outcomes"]] == ["Approach Vector"]


def test_recall_scoped_member_sees_only_scope(mesh, agent):
    remember(agent, "lesson", summary="Lesson one")
    remember(agent, "action", summary="Action one")  # not in the guest's scope
    remember(agent, "thought", summary="Thought one")  # not in the guest's scope

    guest = mesh.member("cloud-agent", kind="agent",
                        permissions=["recall:scoped"],
                        scope={"types": ["lesson", "policy"]})
    seen = search_records(guest)
    assert {r["type"] for r in seen} == {"lesson"}

    pack = recall_context(guest)
    assert pack["outcomes"] == []
    assert len(pack["policies"]) == 0


def test_tool_outputs_never_contain_the_key(agent):
    key_hex = bytes(agent.key).hex()  # the private key, must never be returned
    r = remember(agent, "thought", summary="Private things stay private")
    found = search_records(agent, type="thought")
    pack = recall_context(agent)
    output = json.dumps({"remember": r, "search": found, "context": pack})
    assert key_hex not in output


def test_remember_queues_when_server_unreachable(agent, monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(agent, "flush", boom)

    with pytest.raises(ToolError) as exc:
        remember(agent, "thought", summary="Queued until the next call")
    assert exc.value.code == "queued"
    assert "queued" in exc.value.message

    # The signed record is still in the client's outbox, ready to retry.
    assert [r["seq"] for r in agent.outbox] == [1]


def test_server_module_importable():
    pytest.importorskip("mcp")
    import ltms_mcp.server

    assert ltms_mcp.server.mcp is not None
