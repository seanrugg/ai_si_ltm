"""The member mind's loop (#339), with a fake client and a scripted brain."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ltms_contract import generate_key
from ltms_mind import config
from ltms_mind.agenda import family_of
from ltms_mind.brains.none import NoBrain
from ltms_mind import prompts
from ltms_mind.gate import BAD_REF, DUPLICATE, NO_EVIDENCE, NO_PURPOSE, WRONG_KIND, Gate, LexicalEmbedder
from ltms_mind.loop import CYCLE_TAG, continue_chain
from ltms_mind.brains.base import ALLOWED_KINDS, Context, Proposal, Task

from .conftest import FakeClient, ScriptedBrain, ts

SWARM = {"id": "s1", "topic_id": "sw-00000001", "name": "test-swarm", "role": "member",
         "roster": [{"name": "me", "role": "member", "summary": "board 8 GB · qwen3:4b on board"},
                    {"name": "claude-a", "role": "member", "summary": "cloud · claude-opus-5-5 off board"},
                    {"name": "qwen-b", "role": "member", "summary": "studio · qwen3:30b on board"},
                    {"name": "asker", "role": "member", "summary": ""}]}


def mine(server, name="me"):
    return [r for r in server.records if r["agent"] == name]


def cycle_events(server):
    return [r for r in mine(server) if CYCLE_TAG in (r.get("tags") or [])]


# ------------------------------------------------------------------ duties
def test_duties_read_an_unread_charter(server, make_mind):
    m = make_mind(health=None, dream=config.DreamCondition("never"))
    m.wake()
    assert server.charter_reads == 1
    m.wake()
    assert server.charter_reads == 1  # read once, not every wake


def test_empty_agenda_writes_only_the_cycle_event(server, make_mind):
    out = make_mind(dream=config.DreamCondition("never")).wake()
    recs = mine(server)
    assert [r["type"] for r in recs] == ["event"] and CYCLE_TAG in recs[0]["tags"]
    assert recs[0]["payload"]["mode"] == "awake" and out["tasks"] == 0


def test_dreaming_wake_writes_one_start_end_pair_and_no_separate_cycle_event(server, make_mind):
    make_mind().wake()
    recs = mine(server)
    assert [r["payload"]["phase"] for r in recs] == ["start", "end"]
    assert recs[1]["payload"]["mind"]["mode"] == "dreaming"
    assert len(cycle_events(server)) == 1  # the end event carries the counts


def test_introduction_is_sent_once_per_swarm(server, make_mind):
    server.swarm = SWARM
    m = make_mind(dream=config.DreamCondition("never"))
    m.wake()
    m.wake()
    intros = [r for r in mine(server) if "introduction" in (r.get("tags") or [])]
    assert len(intros) == 1
    server.swarm = {**SWARM, "id": "s2", "name": "other-swarm"}
    m.wake()
    assert len([r for r in mine(server) if "introduction" in (r.get("tags") or [])]) == 2


def test_health_is_reported_on_change_only(server, make_mind):
    levels = {"disk": "ok"}
    m = make_mind(health=lambda: dict(levels), dream=config.DreamCondition("never"))
    m.wake()
    m.wake()
    levels["disk"] = "warn"
    m.wake()
    health = [r for r in mine(server) if "health" in (r.get("tags") or [])]
    assert [r["payload"]["levels"]["disk"] for r in health] == ["ok", "warn"]


def test_pause_means_duties_only(server, make_mind):
    server.swarm = SWARM
    asker = generate_key()
    server.other(asker, "asker", "communication", at=ts(23), summary="What's the battery level?",
                 peers=["me"], tags=["question"])
    server.mind = {"paused": True}
    brain = ScriptedBrain()
    m = make_mind(brain=brain)
    m.wake()
    assert brain.seen == []
    ev = cycle_events(server)
    assert len(ev) == 1 and ev[0]["payload"]["paused"] is True
    assert server.charter_reads == 1  # duties still ran


# ------------------------------------------------------------------ gate
def test_gate_drops_no_evidence_duplicate_and_pure_summary(server, make_mind):
    server.swarm = SWARM
    server.other(generate_key(), "qwen-b", "event", at=ts(22), summary="Wheel motor stalled on the ramp",
                 outcome="stuck")
    server.other(generate_key(), "claude-a", "lesson", at=ts(22, 13),
                 summary="Robots stall on ramps steeper than ten degrees", refs={"about": "qwen-b:1"})

    def script(task):
        if task.category != "reflect":
            return []
        return [
            {"kind": "lesson", "summary": "Ramps need a slower approach speed", "refs": {"agent": "seq"}},
            {"kind": "lesson", "summary": "Robots stall on ramps steeper than ten degrees!",
             "refs": {"about": task.key}},
            {"kind": "summary", "summary": "Today qwen-b got stuck.", "refs": {"about": task.key}},
            {"kind": "lesson", "summary": "Check motor current before climbing; a spike predicts a stall",
             "refs": {"about": task.key}},
        ]

    # the stuck event is already cited by claude-a's lesson, so add an uncited one to reflect on
    server.other(generate_key(), "asker", "action", at=ts(23), summary="Climb the ramp", outcome="failed")
    out = make_mind(brain=ScriptedBrain(script)).wake()
    assert out["gated"] == {BAD_REF: 1, DUPLICATE: 1, NO_PURPOSE: 1}
    lessons = [r for r in mine(server) if r["type"] == "lesson"]
    assert len(lessons) == 1 and lessons[0]["refs"] == {"about": "asker:1", "evidence": ["asker:1"]}
    assert lessons[0]["payload"]["model_family"] == "qwen" and "cycle" in lessons[0]["payload"]


def test_reflect_proposals_cite_their_task_whatever_the_brain_wrote(server, make_mind):
    """#525: v3-c7r:71829 (on q3-u3:58) was a reflect lesson with refs {"agent": "seq"} and no citation."""
    server.swarm = SWARM
    server.other(generate_key(), "asker", "action", at=ts(23), summary="show_icon", outcome="failed")
    seen = []

    def script(task):
        if task.category != "reflect":
            return []
        seen.append(task.key)
        return [
            {"kind": "lesson", "summary": "show_icon only takes blank, check, question, heart or dot"},
            {"kind": "question", "summary": "Which icon did the caller ask for?", "peers": ["asker"],
             "refs": {"evidence": ["v3-c7r:5"]}},
            {"kind": "prediction", "summary": "show_icon fails again within a week unless validated",
             "payload": {"claim": "fails again", "measure": "failed show_icon outcomes",
                         "check_after": "2026-12-04T00:00:00Z"}, "refs": {"evidence": "asker:1"}},
        ]

    out = make_mind(brain=ScriptedBrain(script)).wake()
    assert seen == ["asker:1"] and out["gated"] == {}
    by_kind = {("question" if "question" in (r.get("tags") or []) else r["type"]): r for r in mine(server)
               if "mind" in (r.get("tags") or [])}
    assert by_kind["lesson"]["refs"] == {"evidence": ["asker:1"]}
    assert by_kind["question"]["refs"] == {"evidence": ["asker:1", "v3-c7r:5"]}   # the task's key first, theirs kept
    assert next(r for r in mine(server) if (r.get("payload") or {}).get("claim"))["refs"] == {"evidence": ["asker:1"]}


def test_gate_refuses_refs_that_are_not_record_keys():
    g = Gate(LexicalEmbedder(), 0.92, [])
    for refs in ({"agent": "seq"}, {"evidence": ["<agent>:<seq>"]}, {"evidence": []}, {"about": "q3-u3:0"},
                 {"about": "Q3-U3:5"}, {"about": 58}, {"about": ["q3-u3:58", None]}, ["q3-u3:58"]):
        assert g.check(Proposal("lesson", "A lesson about icons", refs=refs)) == BAD_REF, refs
    assert g.check(Proposal("question", "Which icon?", peers=["q3-u3"], refs={"agent": "seq"})) == BAD_REF
    assert g.check(Proposal("lesson", "Icons must be one of five", refs={"evidence": ["q3-u3:58", "v3-c7r.a_b-1:7"]})) is None
    assert g.check(Proposal("lesson", "Icons must be one of five kinds")) == NO_EVIDENCE   # no refs at all


def test_gate_unit_rules():
    g = Gate(LexicalEmbedder(), 0.92, ["The charger is on the north wall"])
    assert g.check(Proposal("question", "Where is the charger?", peers=["qwen-b"])) is None  # exempt
    assert g.check(Proposal("lesson", "The charger is on the north wall.", refs={"a": "x:1"})) == DUPLICATE
    assert g.check(Proposal("note", "hello", refs={"a": "x:1"})) == NO_PURPOSE
    assert g.check(Proposal("review", "Looks right", refs={"reviews": "x:1"},
                            payload={"verdict": "maybe", "reasons": "r"})) == "bad_proposal"


def test_budget_exhaustion_stops_the_wake(server, make_mind):
    server.swarm = SWARM
    k = generate_key()
    for i in range(4):
        server.other(k, "asker", "communication", at=ts(23, 10 + i), summary=f"Question number {i} about lidar range",
                     peers=["me"], tags=["question"])
    brain = ScriptedBrain(lambda t: [], tokens=900)
    out = make_mind(brain=brain, tokens=1000, max_tasks=5).wake()
    assert len(brain.seen) == 2  # 900 tokens, then 1800 > 1000: no third call
    assert out["stopped"] == "budget_tokens" and out["skipped"] == 1


def test_daily_write_cap(server, make_mind):
    server.swarm = SWARM
    k = generate_key()
    for i, topic in enumerate(["lidar range", "battery voltage", "wheel slip"]):
        server.other(k, "asker", "communication", at=ts(23, 10 + i), summary=f"What is your {topic}?",
                     peers=["me"], tags=["question"])
    brain = ScriptedBrain(lambda t: [{"kind": "answer", "summary": f"Answer to {t.text}"}])
    out = make_mind(brain=brain, daily_writes=2).wake()
    assert out["written"] == 2 and out["gated"] == {"over_budget": 1}


# ------------------------------------------------------------------ agenda
def test_answers_are_bound_to_the_question_and_asker(server, make_mind):
    server.swarm = SWARM
    server.other(generate_key(), "asker", "communication", at=ts(23), summary="Is the lab door open?",
                 peers=["me"], tags=["question"])
    brain = ScriptedBrain(lambda t: [{"kind": "answer", "summary": "I can't see the door from the dock.",
                                      "refs": {"answers": "somebody:99"}, "peers": ["everyone"]}])
    m = make_mind(brain=brain, dream=config.DreamCondition("never"))
    m.wake()
    ans = [r for r in mine(server) if "answer" in (r.get("tags") or [])]
    assert len(ans) == 1 and ans[0]["refs"]["answers"] == "asker:1" and ans[0]["peers"] == ["asker"]
    m.wake()  # answered: not asked again
    assert len(brain.seen) == 1


def test_verify_only_picks_lessons_from_other_families(server, make_mind):
    server.swarm = SWARM
    server.other(generate_key(), "claude-a", "lesson", at=ts(22), summary="Dust lowers lidar range",
                 refs={"about": "x:1"})
    server.other(generate_key(), "qwen-b", "lesson", at=ts(22, 13), summary="Batteries sag in the cold",
                 refs={"about": "x:2"})
    server.other(generate_key(), "outsider", "lesson", at=ts(22, 14), summary="Unknown family lesson",
                 refs={"about": "x:3"})
    brain = ScriptedBrain()
    make_mind(brain=brain).wake()
    reviewed = [t.inputs["record"]["agent"] for t, _ in brain.seen if t.category == "verify"]
    assert reviewed == ["claude-a"]


def test_a_due_numeric_prediction_is_checked_without_a_brain(server, make_mind):
    server.swarm = SWARM
    c = make_mind.client
    c.append("thought", summary="I predict at least 2 stalls this week", tags=["mind", "prediction"],
             ts=ts(20), payload={"claim": "at least 2 stalls", "check_after": ts(23),
                                 "measure": {"count": {"outcome": "stuck"}, "op": ">=", "value": 2}})
    c.append("thought", summary="I predict the dock is moved", tags=["mind", "prediction"], ts=ts(20, 13),
             payload={"claim": "dock moves", "check_after": ts(30), "measure": "ask Sean"})
    c.flush()
    k = generate_key()
    server.other(k, "qwen-b", "event", at=ts(21), summary="stall 1", outcome="stuck")
    server.other(k, "qwen-b", "event", at=ts(22), summary="stall 2", outcome="stuck")
    server.other(k, "qwen-b", "event", at=ts(24), summary="after the window", outcome="stuck")
    out = make_mind(brain=NoBrain(), dream=config.DreamCondition("always")).wake()
    assert out["mode"] == "awake"  # no brain never dreams
    checks = [r for r in mine(server) if "prediction-check" in (r.get("tags") or [])]
    assert len(checks) == 1 and checks[0]["refs"] == {"checks": "me:1"}
    assert checks[0]["payload"]["outcome"] == "true" and checks[0]["payload"]["evidence"]["observed"] == 2.0


def test_study_reads_inside_the_dream_cycle_and_cites_the_source(server, make_mind):
    server.datasets = [{"id": "d1", "name": "manuals", "current_version": 3, "status": "approved"}]
    server.items = {"d1": {"charging.md": "Dock with the contacts facing out."}}

    def script(task):
        if task.category != "study":
            return []
        i = task.inputs
        return [{"kind": "lesson", "summary": "Dock with the contacts facing out",
                 "sources": [{"dataset": i["dataset"], "version": i["version"], "item": i["item"]}]}]
    m = make_mind(brain=ScriptedBrain(script), study_query="charging")
    m.wake()
    lesson = next(r for r in mine(server) if r["type"] == "lesson")
    end = next(r for r in mine(server) if r["payload"].get("phase") == "end")
    assert server.reads == [("d1", "charging.md", lesson["payload"]["cycle"])]
    assert end["payload"]["items_read"] == 1 and end["payload"]["lessons"] == 1
    m.wake()
    assert len(server.reads) == 1  # read once


# ------------------------------------------------------------------ offline
def test_an_offline_outbox_flushes_on_the_next_wake(server, make_mind):
    m = make_mind(dream=config.DreamCondition("never"))
    m.wake()  # online: head is 1 (the cycle event)
    c = m.client
    c.append("event", summary="queued while the network was down")
    server.online = False
    from ltms_mind.loop import save_outbox
    save_outbox(m.cfg.outbox_path, c.outbox)
    assert m.wake().get("offline") is True
    server.online = True
    fresh = FakeClient(server, "me")
    fresh.key = c.key
    out = make_mind(client=fresh, dream=config.DreamCondition("never")).wake()
    assert out["duties"]["outbox_flushed"] == 1
    assert [r["summary"] for r in mine(server)][1] == "queued while the network was down"
    assert out["unsent"] == 0


def test_continue_chain_drops_acked_and_forked_records(server):
    c = FakeClient(server)
    a = c.append("event", summary="a")
    b = c.append("event", summary="b")
    assert continue_chain([a, b], 1, a["hash"]) == [b]
    assert continue_chain([a, b], 0, "f" * 64) == []


# ------------------------------------------------------------------ config and helpers
def test_config_defaults_and_errors():
    cfg = config.load({"LTMS_SERVER": "https://ltms.example.org", "LTMS_KEY_PATH": "/keys/m.key.json"})
    assert cfg.state_path == "/keys/m.key.mind.json" and cfg.brain == "none" and cfg.dream.mode == "never"
    w = config.load({"LTMS_SERVER": "x", "LTMS_KEY_PATH": "k", "MIND_WEIGHTS": "study=0",
                     "MIND_DREAM": "window 23:00-05:00"})
    assert w.weights["study"] == 0 and w.dream.in_window(0) and not w.dream.in_window(12 * 60)
    for bad in ({"LTMS_KEY_PATH": "k"}, {"LTMS_SERVER": "x", "LTMS_KEY_PATH": "k", "MIND_DREAM": "sometimes"},
                {"LTMS_SERVER": "x", "LTMS_KEY_PATH": "k", "MIND_WEIGHTS": "naps=2"}):
        with pytest.raises(config.ConfigError):
            config.load(bad)
    assert config.parse_duration("4h") == 14400 and config.parse_duration("30m") == 1800


def test_family_of():
    assert family_of("qwen3:4b") == "qwen"
    assert family_of("claude-opus-5-5") == "claude"
    assert family_of("genie:qwen3_4b_instruct_2507") == "qwen"
    assert family_of("gpt-5") == "openai"
    assert family_of("bartowski/microsoft_Phi-4-mini") is None  # org/model with a prefix we don't strip
    assert family_of("") is None


def test_idle_dream_condition(server, make_mind):
    c = make_mind.client
    c.append("action", summary="drove", ts="2026-11-24T02:50:00Z")
    c.flush()
    idle = config.DreamCondition("idle", idle_min=30)
    assert make_mind(dream=idle)._dream_due(datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)) is False
    assert make_mind(dream=idle)._dream_due(datetime(2026, 11, 24, 3, 30, tzinfo=timezone.utc)) is True


# ------------------------------------------------------------------ rejection detail (#526)
def test_the_cycle_record_says_why_each_proposal_was_rejected(server, make_mind):
    server.swarm = SWARM
    server.other(generate_key(), "asker", "action", at=ts(23), summary="show_icon", outcome="failed")

    def script(task):
        if task.category != "reflect":
            return []
        return [
            {"kind": "question", "summary": "Why did it fail?"},                       # no peers
            {"kind": "lesson", "summary": "Icons", "refs": {"agent": "seq"}},            # bad ref
            {"kind": "note", "summary": "hello"},                                       # no purpose
            {"kind": "lesson", "summary": "show_icon only takes five icon names"},      # written
            {"kind": "lesson", "summary": "show_icon only takes five icon names!"},     # duplicate
        ]

    make_mind(brain=ScriptedBrain(script)).wake()
    cycle = [r for r in mine(server) if "mind_cycle" in (r.get("tags") or []) and r["payload"].get("phase") == "end"][-1]
    rejected = cycle["payload"]["mind"]["rejected"]
    assert [(r["kind"], r["reason"]) for r in rejected] == [
        ("question", "bad_proposal"), ("lesson", BAD_REF), ("note", NO_PURPOSE), ("lesson", DUPLICATE)]
    assert all(r["task"] == {"category": "reflect", "key": "asker:1"} for r in rejected)
    assert rejected[0]["detail"] == "a question needs peers: who it is put to"
    assert rejected[1]["detail"] == "refs.agent must be <agent>:<seq>, got 'seq'"
    assert rejected[3]["detail"].startswith("near-copy of a recent record (similarity")
    assert "rejected_more" not in cycle["payload"]["mind"]


# ------------------------------------------------------------------ allowed kinds per task (#540)
def test_gate_refuses_a_kind_the_task_does_not_allow():
    """Vector's reflect dreams on q3-u3:58 (72310, 72354, 72413) proposed answers."""
    g = Gate(LexicalEmbedder(), 0.92, [])
    answer = Proposal("answer", "The dock was blocked", refs={"answers": "q3-u3:58"})
    assert g.check(answer, "reflect") == WRONG_KIND
    assert g.detail == "reflect allows lesson, question, prediction; got answer"
    assert g.check(Proposal("lesson", "Keep the dock clear", refs={"evidence": ["q3-u3:58"]}), "review") == WRONG_KIND
    assert g.detail == "review allows review; got lesson"
    assert g.check(answer, "answer") is None                       # allowed where it belongs
    assert g.check(Proposal("note", "hello"), "reflect") == NO_PURPOSE   # an unknown kind is still no_purpose
    assert g.check(Proposal("lesson", "Check the dock before docking", refs={"evidence": ["q3-u3:58"]})) is None
    assert set(ALLOWED_KINDS) == set(prompts.KINDS)               # every task kind has its list


def test_a_reflect_answer_is_rejected_as_wrong_kind_and_the_lesson_still_binds(server, make_mind):
    server.swarm = SWARM
    server.other(generate_key(), "asker", "action", at=ts(23), summary="dock", outcome="failed")

    def script(task):
        if task.category != "reflect":
            return []
        return [{"kind": "answer", "summary": "It failed because the dock was blocked"},
                {"kind": "lesson", "summary": "Clear the path to the dock before a low-battery return"}]

    out = make_mind(brain=ScriptedBrain(script)).wake()
    assert out["gated"] == {WRONG_KIND: 1}
    assert out["rejected"][0]["kind"] == "answer" and out["rejected"][0]["reason"] == WRONG_KIND
    assert out["rejected"][0]["detail"] == "reflect allows lesson, question, prediction; got answer"
    (lesson,) = [r for r in mine(server) if r["type"] == "lesson"]
    assert lesson["refs"] == {"evidence": ["asker:1"]}                # #525 regression: no refs given, still cited


def test_the_prompt_lists_only_the_tasks_kinds_and_a_real_record_key():
    ctx = Context(member="v3-c7r", swarm="echo-base", family="qwen", now="2026-11-28T05:07:00Z", mode="dreaming")
    reflect = Task("reflect", "reflect", "q3-u3:58", "q3-u3 recorded outcome 'failed'", {"record": {}})
    text = prompts.task_prompt(reflect, ctx)
    assert 'one this task allows: "lesson", "question" or "prediction".' in text
    assert '"report"' not in text.split("THE TASK")[0] and "prediction-check" not in text.split("THE TASK")[0]
    assert '{"evidence": ["q3-u3:58"]}' in text and "<agent>:<seq>" not in text
    study = Task("study", "study", "ds1@2/icons", "Study 'icons'", {})
    text = prompts.task_prompt(study, ctx)
    assert 'one this task allows: "lesson".' in text and '{"evidence": ["v3-c7r:120"]}' in text


def test_brain_errors_are_listed_and_the_list_is_capped(server, make_mind):
    server.swarm = SWARM
    for i in range(3):
        server.other(generate_key(), "asker", "action", at=ts(23, i), summary=f"step {i}", outcome="failed")

    class Broken(ScriptedBrain):
        def propose(self, task, ctx):
            raise ValueError("no JSON object in the reply")

    out = make_mind(brain=Broken()).wake()
    assert out["brain_errors"] >= 1 and len(out["rejected"]) == out["brain_errors"]
    assert out["rejected"][0]["reason"] == "brain_error" and out["rejected"][0]["kind"] is None
    assert out["rejected"][0]["detail"] == "ValueError: no JSON object in the reply"

    def many(task):
        return [{"kind": "note", "summary": f"n{i}"} for i in range(14)] if task.category == "reflect" else []

    out = make_mind(brain=ScriptedBrain(many), max_tasks=1).wake()
    assert len(out["rejected"]) == 10 and out["rejected_more"] == 4


def test_gate_detail_is_cleared_on_a_pass():
    g = Gate(LexicalEmbedder(), 0.92, [])
    assert g.check(Proposal("note", "x")) == NO_PURPOSE and g.detail == "unknown kind 'note'"
    assert g.check(Proposal("lesson", "Icons need one of five names", refs={"e": "a:1"})) is None and g.detail is None


# ------------------------------------------------------------------ cross-family review (#521, first live run)
def test_a_reviewer_sees_the_evidence_a_lesson_cites(server, make_mind):
    """M4-C2's first dream: Gemma reviewed Vector's lessons with only their keys to go on."""
    server.swarm = SWARM
    k = generate_key()
    stall = server.other(k, "claude-a", "action", at=ts(21), summary="Climb the ramp", outcome="stuck",
                         payload={"slope_deg": 14, "motor_a": 2.4})
    server.other(k, "claude-a", "lesson", at=ts(22), summary="Ramps over 12 degrees stall the motors",
                 refs={"evidence": [f"claude-a:{stall['seq']}", "claude-a:999"]})
    brain = ScriptedBrain()
    make_mind(brain=brain).wake()
    task = next(t for t, _ in brain.seen if t.category == "verify")
    ev = {e["key"]: e for e in task.inputs["evidence"]}
    assert ev["claude-a:1"]["found"] is True and ev["claude-a:1"]["outcome"] == "stuck"
    assert '"slope_deg": 14' in ev["claude-a:1"]["payload"]
    assert ev["claude-a:999"] == {"key": "claude-a:999", "found": False}


def test_a_record_key_written_as_name_and_number_is_fixed_not_refused(server, make_mind):
    """Gemma's two reviews were gated bad_ref: refs {"v3-c7r": "72120"}."""
    from ltms_mind.loop import normalize_refs
    assert normalize_refs({"v3-c7r": "72120"}) == {"evidence": ["v3-c7r:72120"]}
    assert normalize_refs({"v3-c7r": 72120, "evidence": "q3-u3:58"}) == {"evidence": ["q3-u3:58", "v3-c7r:72120"]}
    assert normalize_refs({"v3-c7r": ["1", "2"]}) == {"evidence": ["v3-c7r:1", "v3-c7r:2"]}
    for left in ({"agent": "seq"}, {"v3-c7r": "0"}, {"v3-c7r": True}, {"reviews": "v3-c7r:5"}, None):
        assert normalize_refs(left) == left

    server.swarm = SWARM
    server.other(generate_key(), "claude-a", "lesson", at=ts(22), summary="Dust lowers lidar range",
                 refs={"agent": "seq"})

    def script(task):
        if task.category != "verify":
            return []
        agent, _, seq = task.key.partition(":")
        return [{"kind": "review", "summary": "Cites only a placeholder: no evidence to check",
                 "refs": {agent: seq}, "payload": {"verdict": "refuted", "reasons": "refs is a template"}}]

    out = make_mind(brain=ScriptedBrain(script)).wake()
    assert out["gated"] == {} and out["written"] == 1
    review = next(r for r in mine(server) if (r.get("payload") or {}).get("verdict"))
    assert review["refs"] == {"evidence": ["claude-a:1"], "reviews": "claude-a:1"}


def test_reads_wait_out_the_rate_limit():
    from ltms_mind.agenda import Agenda

    class Limited(Exception):
        status = 429

    calls, waits = [], []

    class Client:
        def records(self, **kw):
            calls.append(kw)
            if len(calls) < 3:
                raise Limited("429 rate_limited")
            return [{"agent": "x", "seq": 1}]

    a = Agenda(Client(), me="me", swarm=None, family="gemma", now=datetime(2026, 11, 28, tzinfo=timezone.utc),
               since=None, lookback_days=7, corpus=50, study_query="", read_items=set(), sleep=waits.append)
    assert a._records(type="lesson") == [{"agent": "x", "seq": 1}] and waits == [1, 2]
    calls.clear(), waits.clear()
    Client.records = lambda self, **kw: (_ for _ in ()).throw(Limited("429"))
    assert a._records(type="lesson") == [] and waits == [1, 2]          # gives up after three tries


# ------------------------------------------------------------------ server budgets (#342)
def test_a_server_budget_only_ever_lowers_the_device_s(server, make_mind):
    from ltms_mind.loop import _lower
    assert _lower(10, 3) == 3 and _lower(10, 50) == 10 and _lower(10, None) == 10
    assert _lower(10, True) == 10 and _lower(10, -1) == 10 and _lower(10, 0) == 0

    server.swarm = SWARM
    for i in range(4):
        server.other(generate_key(), "asker", "action", at=ts(23, i), summary=f"step {i}", outcome="failed")
    calls = []

    def script(task):
        calls.append(task.key)
        return []

    server.mind = {"paused": False, "budget": {"calls_per_wake": 1, "writes_per_day": 99}}
    out = make_mind(brain=ScriptedBrain(script)).wake()
    assert len(calls) == 1 and out["server_budget"] == {"calls_per_wake": 1, "writes_per_day": 99}
