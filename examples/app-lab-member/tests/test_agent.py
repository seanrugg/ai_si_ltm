"""The on-board agent (#492), off the board: a scripted model, a real MemberLink, no App Lab.

``pytest examples/app-lab-member/tests``
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import Future
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import agent as ag  # noqa: E402
import app_member as am  # noqa: E402
from cli_agent.directives import Decision  # noqa: E402
from cli_agent.member_link import MemberLink  # noqa: E402
from cli_agent.outbox import Outbox  # noqa: E402
from ltms_contract import generate_key  # noqa: E402
from server.tests.conftest import admin, app, config, db_url, http, mesh  # noqa: E402,F401 - fixtures

ON = {"AGENT": "on", "AGENT_BACKEND": "openai", "AGENT_URL": "http://model.test.invalid/v1"}


class Script:
    """A model that answers from a list, and records what it was sent."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages):
        self.seen.append([dict(m) for m in messages])
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


class FakeHost(ag.Host):
    """Runs submitted work at once (the test is its own 'main thread')."""

    def __init__(self, link, name, mcu=None, recall=None):
        self.link, self.mcu_calls = link, []

        def submit(fn):
            fut = Future()
            try:
                fut.set_result(fn(link))
            except Exception as exc:  # noqa: BLE001
                fut.set_exception(exc)
            return fut

        def status():
            return {"running": True, "member": name, "transport": "mqtt", "mqtt": True,
                    "head_seq": link.head["seq"], "unacked": 0}

        def default_mcu(call, value):
            self.mcu_calls.append((call, value))
            return call == "agent_light"  # this pretend board has a spare LED but no display

        super().__init__(name, submit, status, mcu=mcu or default_mcu, recall=recall)


def make_link(tmp_path, name="board-a", key=None):
    published = []
    outbox = Outbox(str(tmp_path / f"{name}.db"))
    link = MemberLink(name, key or generate_key(), outbox, tmp_path / f"{name}-head.json",
                      lambda topic, payload, retain=False: published.append((topic, json.loads(payload))), "sw-test")
    return link, outbox, published


def make_agent(tmp_path, *replies, env=None, name="board-a", key=None, **host_kw):
    link, outbox, published = make_link(tmp_path, name, key)
    settings = ag.load_settings({**ON, **(env or {})})
    host = FakeHost(link, name, **host_kw)
    model = Script(*replies)
    agent = ag.Agent(host, model, settings, ag.Intake(name, settings))
    return agent, model, outbox, published, host


def records(outbox, name="board-a"):
    return outbox.pending(name, 1000)


def swarm_msg(text, sender="board-b", seq=7, peers=None, tags=("swarm-message",), refs=None):
    rec = {"agent": sender, "seq": seq, "summary": text, "tags": list(tags), "peers": list(peers or [])}
    if refs:
        rec["refs"] = refs
    return rec


# ------------------------------------------------------------------ settings
def test_settings_default_off_and_checked():
    assert ag.load_settings({}).enabled is False
    s = ag.load_settings(ON)
    assert s.enabled and s.sources == {"directives", "swarm"} and not s.console and s.max_steps == 6
    assert ag.load_settings({**ON, "CONSOLE": "on"}).sources == {"directives", "swarm", "console"}
    assert ag.load_settings({**ON, "AGENT_BACKEND": "off"}).enabled is False
    for bad in ({"AGENT": "yes"}, {**ON, "AGENT_URL": ""}, {**ON, "AGENT_MAX_STEPS": "0"},
                {**ON, "AGENT_TASKS_FROM": "email"}, {**ON, "CONSOLE": "maybe"}):
        with pytest.raises(ValueError):
            ag.load_settings(bad)


def test_template_has_the_agent_off():
    t = am.TEMPLATE
    assert "AGENT=off" in t and "CONSOLE=off" in t and "NO login" in t
    env = {}
    for line in t.splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    assert ag.load_settings(env).enabled is False


# ------------------------------------------------------------------ the parser
@pytest.mark.parametrize("raw", [
    '{"tool": "note", "args": {"text": "hi"}}',
    '```json\n{"tool": "note", "args": {"text": "hi"}}\n```',
    '<think>I should note {this}</think>\nSure: {"tool": "note", "args": {"text": "hi"}} done',
    '<think>still thinking... {"tool": "note", "args": {"text": "hi"}}',
    'Here you go:\n{"tool": "note", "args": {"text": "hi"}}\nHope that helps.',
])
def test_parser_tolerates_wrappers(raw):
    assert ag.parse_reply(raw) == {"tool": "note", "args": {"text": "hi"}}


def test_parser_answers_and_errors():
    assert ag.parse_reply('{"answer": " ok "}') == {"answer": "ok"}
    assert ag.parse_reply('{"tool": "board_status"}') == {"tool": "board_status", "args": {}}
    assert ag.parse_reply('{"text": "a { brace } inside", "answer": "x"}') == {"answer": "x"}
    for bad in ("", "no json here", '{"foo": 1}', '{"tool": "x", "args": [1]}', '{"answer": ""}', "[1, 2]"):
        with pytest.raises(ag.ParseError):
            ag.parse_reply(bad)


# ------------------------------------------------------------------ intake and loop protection
def test_intake_takes_only_addressed_messages():
    s = ag.load_settings(ON)
    i = ag.Intake("board-a", s)
    assert i.message(swarm_msg("hello everyone")) == "ignored: not addressed to me"
    assert i.message(swarm_msg("hi", peers=["board-a"])) == "queued"
    assert i.message(swarm_msg("@board-a what's your status?")) == "queued"
    assert i.message(swarm_msg("@board-a", sender="board-a")) == "ignored"  # my own echo
    assert i.message(swarm_msg("hi board-a", peers=["board-a"], tags=("swarm-message", "introduction"))) \
        .startswith("ignored")
    assert i.message(swarm_msg("ok", peers=["board-a"], tags=("swarm-message", "directive-ack"))).startswith("ignored")
    # a reply to one of my messages needs to name me again in its text
    assert i.message(swarm_msg("thanks", peers=["board-a"], refs={"reply_to": "board-a:4"})) \
        == "ignored: a reply to me that doesn't name me again"
    assert i.message(swarm_msg("@board-a and then?", peers=["board-a"], refs={"reply_to": "board-a:4"})) == "queued"
    assert i.queue.qsize() == 3
    off = ag.Intake("board-a", ag.load_settings({**ON, "AGENT_TASKS_FROM": "directives"}))
    assert off.message(swarm_msg("@board-a hi")) == "ignored: swarm tasks are off"


def test_intake_limits_per_sender_and_queue():
    t = [0.0]
    s = ag.load_settings({**ON, "AGENT_PER_SENDER_HOUR": "2", "AGENT_QUEUE": "3"})
    i = ag.Intake("board-a", s, clock=lambda: t[0])
    assert i.message(swarm_msg("@board-a 1")) == "queued"
    assert i.message(swarm_msg("@board-a 2")) == "queued"
    assert i.message(swarm_msg("@board-a 3")).startswith("declined: board-b has sent 2")
    assert i.message(swarm_msg("@board-a 1", sender="board-c")) == "queued"
    assert i.message(swarm_msg("@board-a 2", sender="board-c")) == "declined: the task queue is full"
    t[0] = 3601.0
    i.queue.get_nowait()
    assert i.message(swarm_msg("@board-a later")) == "queued"  # an hour on, board-b may ask again


def test_directives_accepted_or_declined_when_busy():
    s = ag.load_settings({**ON, "AGENT_QUEUE": "1"})
    i = ag.Intake("board-a", s)
    d = i.directive({"id": "d1", "text": "report your status", "issued_by": "admin"}, "board-a:3")
    assert isinstance(d, Decision) and d.accept
    busy = i.directive({"id": "d2", "text": "and again", "issued_by": "admin"}, "board-a:4")
    assert not busy.accept and "queue is full" in busy.reason
    task = i.queue.get_nowait()
    assert task.source == "directive" and task.directive == "d1" and task.input_ref == "board-a:3"
    off = ag.Intake("board-a", ag.load_settings({**ON, "AGENT_TASKS_FROM": "swarm"}))
    assert off.directive({"id": "d3", "text": "x"}, None).accept and off.queue.empty()  # followed, not worked


# ------------------------------------------------------------------ the loop and its records
def test_swarm_task_signs_decision_action_and_reply(tmp_path):
    agent, model, outbox, published, host = make_agent(
        tmp_path,
        '<think>check the board</think>{"tool": "board_status", "args": {}}',
        '```json\n{"tool": "set_light", "args": {"state": "done"}}\n```',
        {"answer": "All good: link up over MQTT."})
    agent.intake.message(swarm_msg("@board-a how are you?", seq=12))
    result = agent.run_task(agent.intake.queue.get_nowait())
    assert result == {"status": "done", "answer": "All good: link up over MQTT.", "steps": 2}
    recs = records(outbox)
    assert [r["type"] for r in recs] == ["decision", "action", "decision", "action", "communication"]
    d1, a1, d2, a2, reply = recs
    assert d1["refs"] == {"because": "board-b:12"} and d1["payload"]["tool"] == "board_status"
    assert a1["refs"] == {"decision": "board-a:1"} and a1["outcome"] == "success"
    assert a2["refs"] == {"decision": "board-a:3"} and a2["outcome"] == "success"
    assert host.mcu_calls == [("agent_light", ag.LIGHTS.index("done"))]
    assert reply["peers"] == ["board-b"] and reply["refs"] == {"reply_to": "board-b:12"}
    assert "agent-reply" in reply["tags"] and "swarm-message" in reply["tags"]
    assert published[-1][0].endswith("/say")  # the reply went on the swarm bus
    # the task text is data: it's in the user turn, and the system prompt says so
    assert "DATA" in model.seen[0][0]["content"] and "how are you" in model.seen[0][1]["content"]


def test_unknown_tool_bad_args_and_a_refusing_mcu_are_failed_actions(tmp_path):
    agent, model, outbox, _p, _h = make_agent(
        tmp_path,
        {"tool": "drive_forward", "args": {"m": 1}},
        {"tool": "set_light", "args": {"state": "rainbow"}},
        {"tool": "say", "args": {"text": "hi", "loud": True}},
        {"tool": "show_icon", "args": {"icon": "heart"}},  # this board has no display: the MCU refuses
        {"tool": "note", "args": {"text": 5}},
        {"answer": "I couldn't do much."})
    agent.intake.message(swarm_msg("@board-a try things"))
    result = agent.run_task(agent.intake.queue.get_nowait())
    assert result["status"] == "done" and result["steps"] == 5
    actions = [r for r in records(outbox) if r["type"] == "action"]
    assert [a["outcome"] for a in actions] == ["failed"] * 5
    errors = [a["payload"]["error"] for a in actions]
    assert errors[0] == "unknown tool 'drive_forward'"
    assert "must be one of" in errors[1] and "unknown argument(s): loud" in errors[2]
    assert "refused" in errors[3] and "'text' must be str" in errors[4]
    # each error went back to the model as the tool's result
    assert model.seen[1][-1]["content"].startswith("Result of drive_forward: error: unknown tool")


def test_step_limit(tmp_path):
    agent, _m, outbox, _p, _h = make_agent(
        tmp_path, *[{"tool": "board_status", "args": {}}] * 4, env={"AGENT_MAX_STEPS": "3"})
    agent.intake.message(swarm_msg("@board-a loop forever"))
    result = agent.run_task(agent.intake.queue.get_nowait())
    assert result["status"] == "failed" and result["steps"] == 3 and "stopped after 3 steps" in result["answer"]
    recs = records(outbox)
    assert [r["type"] for r in recs].count("decision") == 3 and recs[-1]["payload"]["status"] == "failed"


def test_parse_retry_once_then_give_up(tmp_path):
    agent, model, _o, _p, _h = make_agent(tmp_path, "sure thing!", {"answer": "ok"})
    agent.intake.message(swarm_msg("@board-a hi"))
    assert agent.run_task(agent.intake.queue.get_nowait())["status"] == "done"
    assert "That wasn't valid: no JSON object found" in model.seen[1][-1]["content"]
    agent2, _m, outbox, _p, _h = make_agent(tmp_path / "b", "nope", "still nope")
    agent2.intake.message(swarm_msg("@board-a hi"))
    r = agent2.run_task(agent2.intake.queue.get_nowait())
    assert r["status"] == "failed" and r["steps"] == 0 and records(outbox)[-1]["type"] == "communication"


def test_say_is_capped_per_task_and_recall_needs_a_client(tmp_path):
    agent, _m, outbox, published, _h = make_agent(
        tmp_path, *[{"tool": "say", "args": {"text": "hello swarm"}}] * 2,
        {"tool": "recall", "args": {"intent": "status"}}, {"answer": "done"}, env={"AGENT_SAY_PER_TASK": "1"})
    agent.intake.message(swarm_msg("@board-a greet"))
    agent.run_task(agent.intake.queue.get_nowait())
    actions = [r for r in records(outbox) if r["type"] == "action"]
    assert [a["outcome"] for a in actions] == ["success", "failed", "failed"]
    assert "say limit reached" in actions[1]["payload"]["error"] and "recall isn't available" in actions[2]["payload"]["error"]
    says = [p for t, p in published if t.endswith("/say") and p["summary"] == "hello swarm"]
    assert len(says) == 1


def test_directive_task_reports_against_the_ack(tmp_path):
    agent, _m, outbox, _p, host = make_agent(tmp_path, {"answer": "Status sent."})
    ack = host.link.append("communication", summary="directive d1: received", tags=["directive-ack"],
                           payload={"directive": "d1-uuid", "status": "received"})
    decision = agent.decide({"id": "d1-uuid", "text": "report status", "issued_by": "admin"})
    assert decision.accept
    agent.run_task(agent.intake.queue.get_nowait())
    report = records(outbox)[-1]
    assert "directive-report" in report["tags"] and report["refs"] == {"because": f"board-a:{ack['seq']}"}
    assert report["payload"]["directive"] == "d1-uuid" and report["payload"]["status"] == "done"


def test_console_task_signs_the_operator_line_first(tmp_path):
    agent, _m, outbox, _p, _h = make_agent(tmp_path, {"answer": "hello operator"}, env={"CONSOLE": "on"})
    assert agent.intake.console("say hi", "c1") is None
    agent.run_task(agent.intake.queue.get_nowait())
    first, last = records(outbox)[0], records(outbox)[-1]
    assert first["type"] == "communication" and "console" in first["tags"] and first["summary"] == "say hi"
    assert last["refs"] == {"reply_to": "board-a:1"} and agent.console_replies["c1"]["answer"] == "hello operator"
    off_agent, *_ = make_agent(tmp_path / "off", {"answer": "x"})
    assert off_agent.intake.console("hi", "c2") == "the console is off"


def test_model_failure_is_reported_not_raised(tmp_path):
    agent, _m, outbox, _p, _h = make_agent(tmp_path, ConnectionError("down"))
    agent.intake.message(swarm_msg("@board-a hi"))
    r = agent.run_task(agent.intake.queue.get_nowait())
    assert r["status"] == "failed" and "couldn't reach my model" in r["answer"]
    assert records(outbox)[-1]["payload"]["status"] == "failed"


def test_a_long_answer_is_cut_in_the_summary_and_kept_in_the_payload(tmp_path):
    agent, _m, outbox, _p, _h = make_agent(tmp_path, {"answer": "x" * 900})
    agent.intake.message(swarm_msg("@board-a essay"))
    agent.run_task(agent.intake.queue.get_nowait())
    reply = records(outbox)[-1]
    assert len(reply["summary"]) == 500 and len(reply["payload"]["answer"]) == 900


def test_worker_thread_runs_queued_tasks(tmp_path):
    import time
    agent, _m, outbox, _p, _h = make_agent(tmp_path, {"answer": "pong"})
    agent.start()
    try:
        agent.on_message(swarm_msg("@board-a ping"))
        for _ in range(50):
            if records(outbox):
                break
            time.sleep(0.05)
    finally:
        agent.stop()
    assert records(outbox) and records(outbox)[-1]["summary"] == "pong"


def test_brick_backend_flattens_the_conversation():
    made = {}

    class FakeLLM:
        def __init__(self, **kw):
            made.update(kw)

        def chat(self, message):
            made["message"] = message
            return '{"answer": "hi"}'

    b = ag.BrickBackend("SYSTEM", timeout=30, factory=FakeLLM)
    out = b.complete([{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "task"}])
    assert out == '{"answer": "hi"}' and made["system_prompt"] == "SYSTEM" and "model" not in made
    assert made["message"].startswith("USER: task") and "SYSTEM" not in made["message"]


def test_openai_backend_request_shape():
    sent = {}

    class Http:
        def post(self, url, json):
            sent.update(url=url, body=json)

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": '{"answer": "x"}'}}]}
            return R()

    b = ag.OpenAIBackend("http://model.test.invalid/v1/", "small-model", http=Http())
    assert b.complete([{"role": "user", "content": "q"}]) == '{"answer": "x"}'
    assert sent["url"] == "http://model.test.invalid/v1/chat/completions" and sent["body"]["model"] == "small-model"


# ------------------------------------------------------------------ the chain, checked by the server
def test_chain_verifies_on_the_server(tmp_path, mesh):
    client = mesh.member("board-a", permissions=("contribute", "recall"))
    agent, _m, outbox, _p, _h = make_agent(
        tmp_path, {"tool": "note", "args": {"text": "asked how I am"}}, {"tool": "nope", "args": {}},
        {"answer": "Fine, thanks."}, key=client.key)
    assert agent.intake.console("how are you?", "c1") == "the console is off"
    agent.intake.message(swarm_msg("@board-a how are you?", seq=3))
    agent.run_task(agent.intake.queue.get_nowait())
    recs = records(outbox)
    assert [r["type"] for r in recs] == ["decision", "thought", "action", "decision", "action", "communication"]
    client.outbox = list(recs)
    res = client.flush()
    assert res[-1]["ok"] and res[-1]["last_ack_seq"] == len(recs)
    stored = client.records(agent="board-a")
    by_seq = {r["seq"]: r for r in stored}
    assert by_seq[3]["refs"] == {"decision": "board-a:1"} and by_seq[3]["outcome"] == "success"
    assert by_seq[5]["refs"] == {"decision": "board-a:4"} and by_seq[5]["outcome"] == "failed"
