"""Filtered recall, context packs, scoped members, recall audit."""
import pytest

from cli_agent.client import LTMSError
from ltms_contract import verify_record


@pytest.fixture
def populated(mesh):
    z3 = mesh.member("z3-r0")
    vec = mesh.member("vector", permissions=["contribute", "recall", "policy"])
    for i, (place, outcome) in enumerate([("living-room", "success"), ("living-room", "reflex_stop"),
                                          ("kitchen", "stuck")]):
        z3.append("decision", summary="Approach Vector", place=place, peers=["vector"])
        z3.append("action", summary="Approach Vector", place=place, peers=["vector"], outcome=outcome,
                  refs={"decision": f"z3-r0:{z3.outbox[-1]['seq']}"}, payload={"distance_m": 0.5 + i / 10})
    z3.append("perception", summary="Floor edge", place="hallway", payload={"value": 512})
    z3.append("lesson", summary="Slow down near Vector", place="living-room", peers=["vector"])
    z3.flush()
    vec.append("communication", summary="Hello Z3", peers=["z3-r0"], payload={"text": "beep"})
    vec.append("policy", summary="No driving after 9pm", payload={"rule": "quiet hours"})
    vec.flush()
    return z3, vec


def test_filters(populated):
    z3, _ = populated
    assert {r["type"] for r in z3.records(type="action")} == {"action"}
    assert len(z3.records(type="action", place="living-room")) == 2
    assert [r["outcome"] for r in z3.records(outcome="stuck")] == ["stuck"]
    assert len(z3.records(peer="vector", type="action")) == 3
    assert len(z3.records(peer="z3-r0")) == 1
    assert len(z3.records(summary="approach")) == 6
    assert z3.records(agent="nobody") == []


def test_returned_records_are_independently_verifiable(populated):
    z3, _ = populated
    for rec in z3.records(agent="z3-r0", limit=200):
        verify_record(rec, bytes(z3.key.verify_key))
    assert z3.records(type="action", include_payload="false")[0].get("payload", "absent") == "absent"


def test_limit_is_capped(populated, app):
    z3, _ = populated
    app.state.config.max_recall = 3
    assert len(z3.records(limit=1000)) == 3


def test_context_pack(populated):
    z3, _ = populated
    pack = z3.context(intent="approach", target="vector", place="living-room")
    assert [r["outcome"] for r in pack["outcomes"]] == ["reflex_stop", "success"]
    assert [r["summary"] for r in pack["comms"]] == ["Hello Z3"]
    assert [r["summary"] for r in pack["lessons"]] == ["Slow down near Vector"]
    assert [r["summary"] for r in pack["policies"]] == ["No driving after 9pm"]
    assert pack["count"] == 5


def test_context_pack_is_capped_at_25(mesh):
    z3 = mesh.member("z3-r0", permissions=["contribute", "recall", "policy"])
    for _ in range(12):
        z3.append("action", summary="Approach Vector", peers=["vector"], outcome="success")
    for _ in range(8):
        z3.append("lesson", summary="Approach slowly", peers=["vector"])
        z3.append("policy", summary="Policy")
    z3.flush()
    pack = z3.context(intent="approach", target="vector")
    assert len(pack["outcomes"]) == 10 and pack["count"] <= 25


def test_scoped_member_sees_only_its_scope(mesh, populated):
    guest = mesh.member("cloud-agent", kind="agent", permissions=["recall:scoped"],
                        scope={"types": ["lesson", "policy"]})
    seen = guest.records(limit=200)
    assert {r["type"] for r in seen} == {"lesson", "policy"}
    assert guest.records(type="action") == []
    pack = guest.context(intent="approach", target="vector")
    assert pack["outcomes"] == [] and len(pack["policies"]) == 1
    blind = mesh.member("blind", kind="agent", permissions=["recall:scoped"])  # no scope granted
    assert blind.records() == []


def test_no_recall_permission(mesh):
    writer = mesh.member("writer", permissions=["contribute"])
    with pytest.raises(LTMSError) as exc:
        writer.records()
    assert exc.value.status == 403


def test_recall_is_audited(populated, admin):
    z3, _ = populated
    z3.records(type="action")
    z3.context(intent="approach")
    entries = admin.get("/api/admin/recall-log").json()["entries"]
    assert [e["detail"]["endpoint"] for e in entries[:2]] == ["context", "records"]
    assert entries[1]["response_count"] == 3
