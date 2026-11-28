"""The local brain (#340): any OpenAI-compatible model server, and the mind embedded in any host."""
from __future__ import annotations

import json
import logging
import threading

import httpx
import pytest

from cli_agent.member_link import MemberLink
from cli_agent.outbox import Outbox
from ltms_contract import GENESIS_HASH
from ltms_mind import brains, config
from ltms_mind.brains.base import Context, Task
from ltms_mind.brains.claude_code import BrainError
from ltms_mind.brains.local import LocalBrain, strip_thinking
from ltms_mind.embed import EmbeddedMind, embedded_config, parse_every

from .conftest import FakeClient

CTX = Context(member="q3-u3", swarm="echo-base", family="qwen", now="2026-11-27T20:00:00Z", mode="dreaming")
TASK = Task(category="obligations", kind="answer", key="me:5", text="Answer the question", inputs={"question": "How long off the dock?"})
PROPOSAL = {"proposals": [{"kind": "answer", "summary": "About 75 to 115 minutes off the dock.",
                           "refs": {"answers": "me:5"}}]}


def server(replies, seen=None):
    """A fake OpenAI-compatible server: each reply is (status, body) or a callable(request)."""
    replies = list(replies)

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append((request, body))
        r = replies.pop(0)
        if callable(r):
            return r(request, body)
        status, payload = r
        return httpx.Response(status, json=payload) if isinstance(payload, dict) else httpx.Response(status, text=payload)
    return httpx.Client(transport=httpx.MockTransport(handle))


def chat(text, usage=True):
    out = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    if usage:
        out["usage"] = {"prompt_tokens": 900, "completion_tokens": 100}
    return 200, out


def brain(replies, seen=None, **kw):
    return LocalBrain(url="http://127.0.0.1:11434/v1", model="qwen3:4b", client=server(replies, seen), **kw)


def test_a_reply_becomes_proposals_and_usage_is_charged():
    seen = []
    b = brain([chat(json.dumps(PROPOSAL))], seen)
    (p,) = b.propose(TASK, CTX)
    assert p.kind == "answer" and p.refs == {"answers": "me:5"}
    assert b.last_tokens == 1000 and b.tokens_estimated is False
    req, body = seen[0]
    assert str(req.url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert body["model"] == "qwen3:4b" and body["response_format"]["type"] == "json_schema"
    assert body["temperature"] == 0.2 and body["max_tokens"] == 1024 and body["stream"] is False
    assert "<data>" in body["messages"][0]["content"]    # the shared templates, data fenced


def test_thinking_and_chatter_around_the_json_are_ignored():
    text = "<think>Let me count the episodes...</think>\nSure! Here you go:\n```json\n" + json.dumps(PROPOSAL) + "\n```"
    assert len(brain([chat(text)]).propose(TASK, CTX)) == 1
    assert strip_thinking("<think>never finished") == ""


def test_missing_usage_is_estimated_and_flagged():
    b = brain([chat(json.dumps(PROPOSAL), usage=False)])
    b.propose(TASK, CTX)
    assert b.last_tokens > 0 and b.tokens_estimated is True


def test_bad_json_is_retried_once_then_dropped():
    b = brain([chat("I think the answer is 75 minutes."), chat(json.dumps(PROPOSAL))])
    assert len(b.propose(TASK, CTX)) == 1
    b = brain([chat("no json"), chat("still none")])
    with pytest.raises(BrainError):
        b.propose(TASK, CTX)


def test_a_server_without_json_mode_is_asked_again_without_it():
    seen = []
    bad = (400, "unknown field: response_format")
    b = brain([bad, bad, chat(json.dumps(PROPOSAL)), chat(json.dumps(PROPOSAL))], seen)
    b.propose(TASK, CTX)
    assert [s[1].get("response_format", {}).get("type") for s in seen] == ["json_schema", "json_object", None]
    assert b.json_mode is False
    b.propose(TASK, CTX)                                   # remembered
    assert "response_format" not in seen[3][1]


# ------------------------------------------------------------------ constrained output (#540)
def test_the_schema_allows_only_the_tasks_kinds_and_record_keys():
    seen = []
    reflect = Task("reflect", "reflect", "q3-u3:58", "Write a lesson", {"record": {}})
    lesson = {"proposals": [{"kind": "lesson", "summary": "Clear the dock path", "refs": {"evidence": ["q3-u3:58"]}}]}
    b = brain([chat(json.dumps(lesson)), chat(json.dumps(PROPOSAL))], seen)
    b.propose(reflect, CTX)
    fmt = seen[0][1]["response_format"]
    item = fmt["json_schema"]["schema"]["properties"]["proposals"]["items"]
    assert fmt["type"] == "json_schema" and item["properties"]["kind"]["enum"] == ["lesson", "question", "prediction"]
    key = item["properties"]["refs"]["additionalProperties"]["anyOf"][0]
    assert key["pattern"] == r"^[a-z0-9][a-z0-9._-]{0,63}:[1-9][0-9]*$"
    b.propose(TASK, CTX)
    assert seen[1][1]["response_format"]["json_schema"]["schema"]["properties"]["proposals"]["items"][
        "properties"]["kind"]["enum"] == ["answer"]


@pytest.mark.parametrize("status", [400, 422])
def test_a_server_that_rejects_the_schema_falls_back_to_json_object(status):
    seen = []
    b = brain([(status, "invalid response_format: json_schema not supported"),
               chat(json.dumps(PROPOSAL)), chat(json.dumps(PROPOSAL))], seen)
    assert len(b.propose(TASK, CTX)) == 1
    assert seen[0][1]["response_format"]["type"] == "json_schema"
    assert seen[1][1]["response_format"] == {"type": "json_object"}
    b.propose(TASK, CTX)                                   # remembered
    assert seen[2][1]["response_format"] == {"type": "json_object"} and b.json_mode is True


def test_decide_asks_for_plain_json_and_other_400s_keep_the_schema():
    seen = []
    b = brain([chat(json.dumps({"accept": True})), (400, "prompt is too long")], seen)
    b.decide({"id": "d1"}, CTX)
    assert seen[0][1]["response_format"] == {"type": "json_object"}
    with pytest.raises(BrainError):
        b.propose(TASK, CTX)
    assert b.format_mode == "schema"


def test_server_errors_and_unreachable_servers_are_brain_errors():
    with pytest.raises(BrainError, match="500"):
        brain([(500, "model not loaded")]).propose(TASK, CTX)

    def refuse(request, body):
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(BrainError, match="can't be reached"):
        brain([refuse]).propose(TASK, CTX)


def test_decide_reads_accept_and_reason():
    b = brain([chat('{"accept": false, "reason": "Battery at level 1."}')])
    assert b.decide({"text": "Explore the lab"}, CTX) == (False, "Battery at level 1.")


def test_the_api_key_is_sent_but_never_logged(caplog):
    seen = []
    b = brain([chat("not json"), chat("still not")], seen, api_key="sk-secret-123")
    with caplog.at_level(logging.DEBUG), pytest.raises(BrainError):
        b.propose(TASK, CTX)
    assert seen[0][0].headers["authorization"] == "Bearer sk-secret-123"
    assert "sk-secret-123" not in caplog.text and "sk-secret-123" not in repr(b)


def test_settings_and_the_ollama_alias():
    env = {"LTMS_SERVER": "https://ltms.example.org", "LTMS_KEY_PATH": "/k/me.key.json", "MIND_BRAIN": "ollama",
           "MIND_LOCAL_URL": "http://127.0.0.1:8091/v1", "MIND_LOCAL_MODEL": "qwen3-4b",
           "MIND_LOCAL_TIMEOUT": "90s", "MIND_LOCAL_API_KEY": "sk-local-777"}
    cfg = config.load(env)
    assert cfg.brain == "local" and cfg.local_timeout == 90 and cfg.local_api_key == "sk-local-777"
    assert "sk-local-777" not in repr(cfg)
    b = brains.make(cfg.brain, cfg)
    assert isinstance(b, LocalBrain) and b.url == "http://127.0.0.1:8091/v1/chat/completions"
    with pytest.raises(ValueError, match="MIND_LOCAL_URL"):
        brains.make("local", config.load({**env, "MIND_LOCAL_URL": ""}))


# -- embedded in a host ------------------------------------------------------------------

def host(tmp_path, srv, name="v3-c7r"):
    client = FakeClient(srv, name=name)
    link = MemberLink(name, client.key, Outbox(tmp_path / "o.db"), tmp_path / "head.json", lambda *a, **k: None, "sw-1")
    return client, link


def test_an_embedded_mind_thinks_with_its_brain_and_signs_into_the_hosts_chain(tmp_path, server):
    client, link = host(tmp_path, server)
    cfg = embedded_config({"LTMS_SERVER": "https://x", "LTMS_KEY_PATH": "/k/v.key.json"}, str(tmp_path),
                          force={"MIND_DREAM": "always", "MIND_EMBEDDER": "lexical", "MIND_HEALTH": "off"})
    assert cfg.state_path == str(tmp_path / "mind.json")
    b = brain([chat(json.dumps({"proposals": []}))] * 10)
    mind = EmbeddedMind(cfg, 3600, client, link.append, lambda: link.head, threading.Lock(), 0.0, brain=b)
    counts = mind.tick(0.0)
    assert counts["brain"] == "local" and counts["mode"] == "dreaming"
    recs = link._outbox.pending("v3-c7r", limit=100)
    prev = GENESIS_HASH
    for r in recs:
        assert r["prev_hash"] == prev
        prev = r["hash"]
    assert any(r["summary"] == "Dream cycle ended" for r in recs)
    assert mind.tick(10.0) is None and mind.due(3600.0)   # waits for its interval


def test_an_embedded_mind_never_raises_into_its_host(tmp_path, server):
    server.online = False
    client, link = host(tmp_path, server)
    cfg = embedded_config({"LTMS_SERVER": "https://x", "LTMS_KEY_PATH": "/k/v.key.json"}, str(tmp_path),
                          force={"MIND_EMBEDDER": "lexical"})
    mind = EmbeddedMind(cfg, 60, client, link.append, lambda: link.head, threading.Lock(), 0.0)
    mind.tick(0.0)


def test_parse_every():
    assert parse_every({"X": "15m"}, "X", 1.0) == 900 and parse_every({}, "X", 7.0) == 7.0
    assert parse_every({"X": "0"}, "X", 7.0) == 0
    with pytest.raises(ValueError):
        parse_every({"X": "soon"}, "X", 1.0)
