"""Hermes recorder plugin (#469): plain functions driven with fake hook payloads."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import httpx
import pytest

from cli_agent.client import LTMSClient
from cli_agent.outbox import Outbox
from ltms_contract import verify_record

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations" / "hermes"))

import ltms_recorder  # noqa: E402
from ltms_recorder.recorder import (  # noqa: E402
    MAX_PAYLOAD_BYTES,
    Recorder,
    looks_like_error,
    redact,
    tool_call_record,
    resolve_settings,
)

VECTOR_MAP = {"mcp__vector__": "vector-1"}


@pytest.fixture
def agent(mesh):
    return mesh.member("hermes-test", kind="agent")


def _records(client):
    recs = client.records(agent=client.name, limit=100)
    return sorted(recs, key=lambda r: r["seq"])


def _offline(*_a, **_k):
    raise httpx.ConnectError("unreachable")


def _session(rec: Recorder, calls=(("mcp__vector__vector_status", {}, '{"battery_level": 3}'),)):
    rec.on_session_start(session_id="s1", model="nemotron-3-nano-64k", platform="cli")
    for name, args, result in calls:
        rec.on_post_tool_call(tool_name=name, args=args, result=result, task_id="t1", duration_ms=12)
    rec.on_session_end(session_id="s1", completed=True, interrupted=False, model="nemotron-3-nano-64k")


def test_session_and_tool_calls_become_verified_chained_records(agent, tmp_path):
    rec = Recorder(agent, settings={"peer_map": VECTOR_MAP}, outbox=Outbox(tmp_path / "o.db"),
                   head_path=tmp_path / "head.json")
    _session(rec, calls=(
        ("mcp__vector__vector_status", {}, '{"battery_level": 3, "is_charging": false}'),
        ("mcp__vector__vector_say", {"text": "Hello"}, '{"status": "ok"}'),
    ))
    assert rec.flush() == 0  # on_session_end already flushed (no background thread)
    recs = _records(agent)
    assert [r["type"] for r in recs] == ["event", "action", "action", "event"]
    for prev, cur in zip(recs, recs[1:]):
        assert cur["prev_hash"] == prev["hash"]
    for r in recs:
        verify_record(r, bytes(agent.key.verify_key))
    status = recs[1]
    assert status["payload"]["result_raw"] == '{"battery_level": 3, "is_charging": false}'
    assert status["payload"]["ok"] is True and status["payload"]["session_id"] == "s1"
    assert recs[3]["payload"]["tool_calls"] == 2 and recs[3]["payload"]["tool_errors"] == 0
    assert json.loads((tmp_path / "head.json").read_text())["seq"] == 4


def test_peer_map_links_the_robot(agent):
    rec = Recorder(agent, settings={"peer_map": VECTOR_MAP})
    _session(rec, calls=(("mcp__vector__vector_say", {"text": "Hello"}, '{"status": "ok"}'),))
    say = _records(agent)[1]
    assert say["peers"] == ["vector-1"]
    assert say["summary"].startswith("mcp__vector__vector_say ok")


def test_large_results_are_truncated_and_payload_stays_under_limit():
    settings = resolve_settings({"max_result_bytes": 100})
    _, payload, _ = tool_call_record(tool_name="t", args={"blob": "a" * 50_000}, result="é" * 10_000,
                                     task_id=None, duration_ms=1, session_id=None, model=None,
                                     settings=settings)
    assert payload["result_truncated"] is True
    assert len(payload["result_raw"].encode("utf-8")) <= 100
    assert payload["args_truncated"] is True
    assert len(json.dumps(payload).encode("utf-8")) <= MAX_PAYLOAD_BYTES

    huge = resolve_settings({"max_result_bytes": 10**9})  # capped by resolve_settings
    _, payload, _ = tool_call_record(tool_name="t", args={}, result="x" * 100_000, task_id=None,
                                     duration_ms=None, session_id=None, model=None, settings=huge)
    assert len(json.dumps(payload).encode("utf-8")) <= MAX_PAYLOAD_BYTES


def test_redaction_is_nested_and_case_insensitive(agent):
    args = {"api_key": "x", "nested": {"Token": "y", "keep": "z"}, "items": [{"Password": "p"}]}
    assert redact(args, resolve_settings(None)["redact_keys"]) == {
        "api_key": "[redacted]", "nested": {"Token": "[redacted]", "keep": "z"}, "items": [{"Password": "[redacted]"}]}
    rec = Recorder(agent)
    _session(rec, calls=(("tool", args, "done"),))
    recorded = _records(agent)[1]["payload"]["args"]
    assert recorded["api_key"] == "[redacted]" and recorded["nested"]["Token"] == "[redacted]"
    assert "x" not in json.dumps(recorded["api_key"]) and recorded["nested"]["keep"] == "z"


def test_summary_mode_drops_content_and_off_mode_records_sessions_only(mesh):
    a = mesh.member("hermes-summary", kind="agent")
    _session(Recorder(a, settings={"record_mode": "summary"}), calls=(("tool", {"q": "secret plan"}, "result"),))
    call = _records(a)[1]["payload"]
    assert "args" not in call and "result_raw" not in call and call["tool_name"] == "tool"

    b = mesh.member("hermes-off", kind="agent")
    _session(Recorder(b, settings={"record_mode": "off"}), calls=(("tool", {}, "Error: boom"), ("tool", {}, "ok")))
    recs = _records(b)
    assert [r["type"] for r in recs] == ["event", "event"]
    assert recs[1]["payload"]["tool_calls"] == 2 and recs[1]["payload"]["tool_errors"] == 1


def test_offline_never_raises_and_records_are_sent_later(agent, tmp_path):
    outbox_path, head = tmp_path / "o.db", tmp_path / "head.json"
    rec = Recorder(agent, outbox=Outbox(outbox_path), head_path=head)
    _session(rec)  # online: 3 records, head saved
    online_http = agent.http

    agent.http = httpx.Client(transport=httpx.MockTransport(_offline), base_url="http://offline")
    rec.on_session_start(session_id="s2")
    rec.on_post_tool_call(tool_name="tool", args={}, result="ok", task_id="t", duration_ms=1)
    assert rec.flush() == 0          # no exception
    assert rec.queued == 2

    # A restart while still offline continues the chain from the saved head and the outbox.
    client2 = LTMSClient(name=agent.name, signing_key=agent.key,
                         http=httpx.Client(transport=httpx.MockTransport(_offline), base_url="http://offline"))
    rec2 = Recorder(client2, outbox=Outbox(outbox_path), head_path=head)
    rec2.on_session_end(session_id="s2", completed=True)
    assert rec2.queued == 3          # 2 restored from the outbox + the session end

    client2.http = online_http
    assert rec2.flush() == 3
    agent.http = online_http
    recs = _records(agent)
    assert [r["seq"] for r in recs] == [1, 2, 3, 4, 5, 6]
    for r in recs:
        verify_record(r, bytes(agent.key.verify_key))


def test_waiting_records_are_sent_after_restart_with_no_new_events(agent, tmp_path):
    outbox_path, head = tmp_path / "o.db", tmp_path / "head.json"
    Recorder(agent, outbox=Outbox(outbox_path), head_path=head).flush()  # online once: head saved
    online_http = agent.http
    agent.http = httpx.Client(transport=httpx.MockTransport(_offline), base_url="http://offline")
    offline = Recorder(agent, outbox=Outbox(outbox_path), head_path=head)
    offline.on_session_start(session_id="s")
    offline.flush()
    assert offline.queued == 1

    agent.http = online_http
    restarted = Recorder(agent, outbox=Outbox(outbox_path), head_path=head)
    assert restarted.flush() == 1     # nothing new queued, the waiting record still goes
    assert [r["seq"] for r in _records(agent)] == [1]


def test_no_head_and_offline_keeps_events_unsigned(agent):
    agent.http = httpx.Client(transport=httpx.MockTransport(_offline), base_url="http://offline")
    rec = Recorder(agent)
    rec.on_session_start(session_id="s")
    assert rec.flush() == 0
    assert rec.ready is False and rec.queued == 1 and agent.outbox == []


def test_key_never_leaks(agent, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    key_hex = bytes(agent.key).hex()
    outbox = Outbox(tmp_path / "o.db")
    rec = Recorder(agent, outbox=outbox, head_path=tmp_path / "head.json")
    returned = [rec.on_session_start(session_id="s"),
                rec.on_post_tool_call(tool_name="tool", args={"a": 1}, result="ok", task_id=1, duration_ms=1),
                rec.on_session_end(session_id="s", completed=True), rec.flush()]
    stored = _records(agent)
    agent.http = httpx.Client(transport=httpx.MockTransport(_offline), base_url="http://offline")
    rec.on_post_tool_call(tool_name="tool", args={}, result="ok")
    returned.append(rec.flush())
    local = outbox.pending(agent.name, 1000) + list(agent.outbox)
    assert stored and local
    for r in stored + local:
        assert key_hex not in json.dumps(r)
    assert key_hex not in caplog.text
    assert key_hex not in repr(returned)
    assert key_hex not in (tmp_path / "head.json").read_text()


def test_hooks_ignore_unknown_payload_fields_and_bad_input(agent):
    rec = Recorder(agent)
    rec.on_session_start(session_id="s", model="m", platform="cli", future_field=1)
    rec.on_post_tool_call(tool_name=None, args=object(), result=b"\xff bytes", future_field=2)
    rec.on_session_end(future_field=3)
    assert [r["type"] for r in _records(agent)] == ["event", "action", "event"]


def test_error_detection():
    assert looks_like_error('{"error": "not found"}')
    assert looks_like_error('{"success": false}')
    assert looks_like_error("Error: timed out")
    assert looks_like_error({"isError": True})
    assert not looks_like_error('{"battery_level": 3}')
    assert not looks_like_error("Hello")


class _FakeCtx:
    def __init__(self, config=None):
        self.hooks, self.config = {}, config or {}

    def get_config(self):
        return self.config

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def test_register_wires_three_hooks(monkeypatch, tmp_path):
    from ltms_contract import generate_key, save_key
    key = tmp_path / "k.json"
    save_key(key, "hermes-reg", generate_key())
    monkeypatch.setenv("LTMS_SERVER", "http://127.0.0.1:9")
    monkeypatch.setenv("LTMS_KEY_PATH", str(key))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(ltms_recorder.atexit, "register", lambda fn: None)
    ctx = _FakeCtx({"record_mode": "summary", "rollup": False})  # rollup wiring: test_hermes_rollup.py
    ltms_recorder.register(ctx)
    assert set(ctx.hooks) == {"on_session_start", "post_tool_call", "on_session_end"}
    recorder = ctx.hooks["post_tool_call"].__self__
    assert recorder.settings["record_mode"] == "summary"
    recorder._stop.set()
    recorder._wake.set()


def test_register_without_env_does_nothing(monkeypatch):
    monkeypatch.delenv("LTMS_SERVER", raising=False)
    monkeypatch.delenv("LTMS_KEY_PATH", raising=False)
    ctx = _FakeCtx()
    ltms_recorder.register(ctx)  # must not raise
    assert ctx.hooks == {}
