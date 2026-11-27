"""Directives in the mind (#341): pulled over HTTPS, verified, decided, acknowledged,
worked on as obligations, and closed by a report."""
from __future__ import annotations

import json
import uuid

from nacl.signing import SigningKey

from ltms_contract import canonicalize
from ltms_mind import config
from ltms_mind.gate import BAD_PROPOSAL

from .conftest import ScriptedBrain

FLEET = SigningKey.generate()
LEADER = SigningKey.generate()
NEVER = config.DreamCondition("never")
SWARM = {"id": "s1", "topic_id": "sw-00000001", "name": "test-swarm", "role": "member",
         "leader": {"name": "lead-a", "public_key": LEADER.verify_key.encode().hex()},
         "roster": [{"name": "me", "role": "member", "summary": "board · qwen3:4b on board"},
                    {"name": "lead-a", "role": "member", "summary": "cloud · claude-opus-5-5 off board"}]}


def directive(text="Dock by 22:00", *, signer="fleet", key=FLEET, issued_by="admin",
              expires="2026-11-30T00:00:00Z", did=None) -> dict:
    did = did or str(uuid.uuid4())
    stmt = {"v": 1, "id": did, "scope": {"swarm": "s1"}, "text": text, "payload": None, "issued_by": issued_by,
            "issued_at": "2026-11-23T12:00:00Z", "expires_at": expires}
    raw = canonicalize(stmt).decode()
    sig = key.sign(b"ltms-directive-v1\n" + raw.encode()).signature.hex()
    return {"kind": "directive", "id": did, "statement": raw, "signature": sig, "signer": signer,
            "issued_by": issued_by}


def cancel(did: str, key=FLEET) -> dict:
    raw = canonicalize({"v": 1, "id": did, "cancelled_at": "2026-11-23T13:00:00Z"}).decode()
    return {"kind": "cancel", "id": did, "statement": raw,
            "signature": key.sign(b"ltms-directive-cancel-v1\n" + raw.encode()).signature.hex()}


def setup(server, *items):
    server.swarm = SWARM
    server.fleet_public = FLEET.verify_key.encode().hex()
    server.directives = list(items)


def mine(server, tag):
    return [r for r in server.records if r["agent"] == "me" and tag in (r.get("tags") or [])]


def acks(server):
    return [(r["payload"]["status"], r["payload"].get("reason")) for r in mine(server, "directive-ack")]


class Decider(ScriptedBrain):
    def __init__(self, view=None, fail=False, **kw):
        super().__init__(**kw)
        self.view, self.fail, self.asked = view, fail, []

    def decide(self, stmt, context):
        self.asked.append(stmt["text"])
        if self.fail:
            raise RuntimeError("model down")
        return self.view


def test_a_fleet_directive_is_acknowledged_worked_on_and_closed_by_a_report(server, make_mind):
    d = directive()
    setup(server, d)
    brain = ScriptedBrain(lambda t: [{"kind": "report", "summary": "Docked at 21:40.",
                                      "payload": {"status": "done", "evidence": "on charger"}}]
                          if t.kind == "directive" else [])
    m = make_mind(brain=brain, dream=NEVER)
    out = m.wake()
    assert acks(server) == [("received", None), ("accepted", None)]
    assert out["duties"]["directive_accepted"] == 1
    task = brain.seen[0][0]
    assert task.kind == "directive" and task.key == d["id"] and "Dock by 22:00" in task.text
    accepted = mine(server, "directive-ack")[1]
    report = mine(server, "directive-report")[0]
    assert report["type"] == "communication"
    assert report["refs"] == {"acts_on": f"me:{accepted['seq']}"}
    assert report["payload"]["directive"] == d["id"] and report["payload"]["status"] == "done"
    assert not report.get("peers")  # issued by an admin, not a member

    m.wake()  # acknowledged once, and done: not on the agenda again
    assert len(mine(server, "directive-ack")) == 2 and len(brain.seen) == 1


def test_a_partial_report_keeps_the_directive_open(server, make_mind):
    setup(server, directive())
    brain = ScriptedBrain(lambda t: [{"kind": "report", "summary": f"Halfway there ({len(brain.seen)}).",
                                      "payload": {"status": "partial"}}] if t.kind == "directive" else [])
    m = make_mind(brain=brain, dream=NEVER, novelty=0.999)
    m.wake()
    m.wake()
    assert [t.kind for t, _ in brain.seen] == ["directive", "directive"]
    assert brain.seen[1][0].inputs["earlier_reports"]


def test_the_brain_may_decline_with_a_reason(server, make_mind):
    setup(server, directive("Drive to the stairs"))
    brain = Decider(view=(False, "the stairs are outside my safety envelope"))
    make_mind(brain=brain, dream=NEVER).wake()
    assert acks(server) == [("received", None), ("declined", "the stairs are outside my safety envelope")]
    assert not [t for t, _ in brain.seen if t.kind == "directive"]


def test_no_view_a_failing_brain_or_a_reasonless_decline_means_follow(server, make_mind, tmp_path):
    for i, brain in enumerate((Decider(view=None), Decider(fail=True), Decider(view=(False, "  ")))):
        setup(server, directive())
        make_mind(brain=brain, dream=NEVER, state_path=str(tmp_path / f"s{i}.mind.json")).wake()
        assert acks(server)[-1] == ("accepted", None), brain.view


def test_the_decision_is_charged_to_the_budget(server, make_mind):
    setup(server, directive(), directive("Report your battery"))
    brain = Decider(view=(True, None))
    make_mind(brain=brain, dream=NEVER, brain_calls=1).wake()
    assert brain.asked == ["Dock by 22:00"]  # the second decision had no budget left: followed
    assert [s for s, _ in acks(server)].count("accepted") == 2


def test_a_bad_signature_is_recorded_once_and_never_followed(server, make_mind):
    forged = directive(key=SigningKey.generate())
    setup(server, forged)
    brain = ScriptedBrain()
    m = make_mind(brain=brain, dream=NEVER)
    m.wake()
    m.wake()
    assert not acks(server) and len(mine(server, "directive-invalid")) == 1
    assert not brain.seen


def test_a_leader_directive_is_verified_with_the_leaders_key_and_reported_to_the_leader(server, make_mind):
    setup(server, directive("Map the hallway", signer="leader", key=LEADER, issued_by="lead-a"))
    brain = ScriptedBrain(lambda t: [{"kind": "report", "summary": "Couldn't: no camera.",
                                      "payload": {"status": "couldnt", "reason": "I have no camera"}}]
                          if t.kind == "directive" else [])
    make_mind(brain=brain, dream=NEVER).wake()
    assert acks(server)[-1] == ("accepted", None)
    assert "your swarm leader lead-a" in brain.seen[0][0].text
    report = mine(server, "directive-report")[0]
    assert report["peers"] == ["lead-a"] and report["payload"]["status"] == "couldnt"


def test_a_couldnt_report_needs_a_reason(server, make_mind):
    setup(server, directive())
    brain = ScriptedBrain(lambda t: [{"kind": "report", "summary": "Couldn't.", "payload": {"status": "couldnt"}}]
                          if t.kind == "directive" else [])
    out = make_mind(brain=brain, dream=NEVER).wake()
    assert out["gated"] == {BAD_PROPOSAL: 1} and not mine(server, "directive-report")


def test_a_question_for_help_points_at_the_directive(server, make_mind):
    setup(server, directive())
    brain = ScriptedBrain(lambda t: [{"kind": "question", "summary": "Where is the dock?", "peers": ["lead-a"]}]
                          if t.kind == "directive" else [])
    make_mind(brain=brain, dream=NEVER).wake()
    q = mine(server, "question")[0]
    assert set(q["refs"]) == {"acts_on"} and q["peers"] == ["lead-a"]


def test_a_cancel_closes_an_open_directive(server, make_mind):
    d = directive()
    setup(server, d)
    brain = ScriptedBrain()
    m = make_mind(brain=brain, dream=NEVER)
    m.wake()
    assert [t.kind for t, _ in brain.seen] == ["directive"]
    server.directives = [cancel(d["id"])]
    out = m.wake()
    assert out["duties"]["directive_cancelled"] == 1 and len(brain.seen) == 1
    out = m.wake()
    assert "directive_cancelled" not in out["duties"]  # listed for 7 days, counted once


def test_an_expired_directive_leaves_the_agenda(server, make_mind, tmp_path):
    setup(server, directive(expires="2026-11-24T04:00:00Z"))
    brain = ScriptedBrain()
    m = make_mind(brain=brain, dream=NEVER)
    m.wake()
    assert len(brain.seen) == 1
    later = make_mind(brain=brain, dream=NEVER, now=m.now().replace(hour=5))
    server.directives = []
    later.wake()
    assert len(brain.seen) == 1


def test_a_paused_mind_leaves_directives_for_later(server, make_mind):
    setup(server, directive())
    server.mind = {"paused": True}
    m = make_mind(dream=NEVER)
    m.wake()
    assert not acks(server)
    server.mind = {}
    m.wake()
    assert [s for s, _ in acks(server)] == ["received", "accepted"]


def test_an_old_server_without_the_endpoint_doesnt_stop_the_wake(server, make_mind):
    server.directives = None
    out = make_mind(dream=NEVER).wake()
    assert out["duties"]["directives_unavailable"] == 1 and "offline" not in out


def test_a_brain_with_no_view_spends_no_budget_on_decisions(server, make_mind):
    setup(server, directive(), directive("Report your battery"))
    brain = ScriptedBrain(lambda t: [{"kind": "report", "summary": f"Done: {t.inputs['directive']['text']}",
                                      "payload": {"status": "done"}}] if t.kind == "directive" else [])
    out = make_mind(brain=brain, dream=NEVER, brain_calls=2).wake()
    assert [s for s, _ in acks(server)].count("accepted") == 2
    assert len(mine(server, "directive-report")) == 2 and out["budget"]["brain_calls"] == 2
