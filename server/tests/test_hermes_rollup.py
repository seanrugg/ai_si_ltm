"""Hermes daily usage rollup (#470): counters, privacy, day rollover, restart, emission."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cli_agent.outbox import Outbox
from ltms_contract import verify_record

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations" / "hermes"))

import ltms_recorder  # noqa: E402
from ltms_recorder.recorder import Recorder  # noqa: E402
from ltms_recorder.rollup import (  # noqa: E402
    MAX_TOOL_KEYS,
    Rollup,
    api_error_class,
    error_reason,
    route_key,
    tool_key,
)

ARGS_SENTINEL = "ARGS-SENTINEL-7f3a"
RESULT_SENTINEL = "RESULT-SENTINEL-91c2"
ERROR_SENTINEL = "ERRTEXT-SENTINEL-55d0"
PATH_SENTINEL = "/home/someone/PATH-SENTINEL-0b1e/secret.txt"
SENTINELS = (ARGS_SENTINEL, RESULT_SENTINEL, ERROR_SENTINEL, PATH_SENTINEL, "PATH-SENTINEL")


class Clock:
    def __init__(self, day="2026-11-22"):
        self.day = day

    def __call__(self):
        return self.day


@pytest.fixture
def agent(mesh):
    return mesh.member("hermes-rollup", kind="agent")


def _setup(agent, tmp_path, clock, settings=None):
    rec = Recorder(agent, settings=settings or {}, outbox=Outbox(tmp_path / "o.db"),
                   head_path=tmp_path / "head.json")
    roll = Rollup(tmp_path / "data", plugin_version="0.2.0", today=clock)
    return rec, roll, ltms_recorder.wire(rec, roll)


def _rollups(agent):
    recs = agent.records(agent=agent.name, limit=200)
    return [r for r in recs if "agent-rollup" in (r.get("tags") or [])]


def _simulated_day(hooks):
    """3 sessions, 10 tool calls across 3 tools (2 errors), 4 API calls on 2 models, 1 compaction."""
    calls = [
        ("mcp__vector__vector_status", {"q": ARGS_SENTINEL}, json.dumps({"battery": RESULT_SENTINEL})),
        ("mcp__vector__vector_status", {}, '{"battery_level": 3}'),
        ("mcp__vector__vector_status", {}, '{"battery_level": 3}'),
        ("mcp__vector__vector_say", {"text": ARGS_SENTINEL}, '{"status": "ok"}'),
        ("mcp__vector__vector_say", {"text": "hi"}, json.dumps({"error": f"request timed out {ERROR_SENTINEL}"})),
        ("mcp__vector__vector_say", {"text": "hi"}, '{"status": "ok"}'),
        ("read_file", {"path": PATH_SENTINEL}, RESULT_SENTINEL),
        ("read_file", {"path": PATH_SENTINEL}, f"Error: no such file {PATH_SENTINEL}"),
        ("read_file", {"path": "x"}, "contents"),
        ("read_file", {"path": "y"}, "contents"),
    ]
    i = 0
    for s in range(3):
        hooks["on_session_start"](session_id=f"s{s}", model="nemotron-3-nano-64k", platform="cli")
        for name, args, result in calls[i:i + 4] if s < 2 else calls[i:]:
            hooks["post_tool_call"](tool_name=name, args=args, result=result, task_id="t", duration_ms=10)
        i += 4
        hooks["on_session_end"](session_id=f"s{s}", completed=s != 2, interrupted=s == 2)
    usage = {"input_tokens": 1000, "output_tokens": 100}
    hooks["post_api_request"](provider="custom", model="nemotron-3-nano-64k", usage=usage, api_duration=2.5,
                              messages=[{"content": ARGS_SENTINEL}])
    hooks["post_api_request"](provider="custom", model="nemotron-3-nano-64k", usage=usage, api_duration=1.5)
    hooks["post_api_request"](provider="custom", model="qwen2.5:1.5b",
                              usage={"prompt_tokens": 50, "completion_tokens": 5}, api_duration=0.5)
    hooks["post_api_request"](provider=f"http://127.0.0.1:11434/v1{PATH_SENTINEL}", model="qwen2.5:1.5b",
                              usage=None, api_duration=None)
    hooks["api_request_error"](status_code=503, retryable=True, error=ERROR_SENTINEL)
    hooks["api_request_error"](status_code=None, retryable=True)
    hooks["post_compaction"](summary=RESULT_SENTINEL)


def test_simulated_day_becomes_one_rollup_record(agent, tmp_path):
    clock = Clock()
    rec, roll, hooks = _setup(agent, tmp_path, clock)
    _simulated_day(hooks)
    assert _rollups(agent) == []  # nothing sent during the day
    assert roll.path("2026-11-22").exists()

    clock.day = "2026-11-23"
    hooks["on_session_start"](session_id="n1", model="m", platform="cli")  # first hook after midnight
    rec.flush()
    rolls = _rollups(agent)
    assert len(rolls) == 1
    r = rolls[0]
    verify_record(r, bytes(agent.key.verify_key))
    assert r["type"] == "perception" and r["tags"] == ["telemetry", "internal", "agent-rollup"]
    assert r["summary"] == "daily rollup 2026-11-22: 3 sessions, 10 tool calls, 2 errors"
    p = r["payload"]
    assert p["rollup_schema"] == 1 and p["date"] == "2026-11-22"
    assert p["sessions"] == {"started": 3, "completed": 2, "interrupted": 1}
    assert p["tool_calls"] == {"mcp__vector__vector_status": {"ok": 3, "error": 0},
                               "mcp__vector__vector_say": {"ok": 2, "error": 1},
                               "read_file": {"ok": 3, "error": 1}}
    assert p["tool_errors"] == {"timeout": 1, "not_found": 1}
    assert p["model_routes"]["custom/nemotron-3-nano-64k"] == {
        "calls": 2, "input_tokens": 2000, "output_tokens": 200, "api_seconds": 4.0}
    assert p["model_routes"]["custom/qwen2.5:1.5b"]["calls"] == 2  # the URL provider became "custom"
    assert p["model_routes"]["custom/qwen2.5:1.5b"]["input_tokens"] == 50
    assert p["api_errors"] == {"5xx": 1, "network": 1}
    assert p["compactions"] == 1
    assert set(p["machine"]) == {"arch", "os", "ram_gb", "hermes_version", "plugin_version"}
    assert "_emitted" not in p

    # Accepted -> the day file goes at the next hook; the new day keeps counting.
    assert roll.path("2026-11-22").exists()
    hooks["post_compaction"]()
    assert not roll.path("2026-11-22").exists()
    assert roll.path("2026-11-23").exists()
    hooks["post_compaction"]()
    rec.flush()
    assert len(_rollups(agent)) == 1  # never sent twice


def test_rollup_never_contains_content(agent, tmp_path):
    clock = Clock()
    rec, roll, hooks = _setup(agent, tmp_path, clock, settings={"record_mode": "off"})
    _simulated_day(hooks)
    for text in (roll.path("2026-11-22").read_text(),):
        for s in SENTINELS:
            assert s not in text
    clock.day = "2026-11-23"
    hooks["post_compaction"]()
    rec.flush()
    (r,) = _rollups(agent)
    blob = json.dumps(r)
    for s in SENTINELS:
        assert s not in blob
    assert "127.0.0.1" not in blob and "http" not in json.dumps(r["payload"])


def test_unknown_errors_and_the_65th_tool_count_as_other(tmp_path):
    assert error_reason('{"error": "kaboom"}') == "other"
    assert error_reason(12345) == "other"
    assert error_reason("Error: permission denied") == "refused"
    assert error_reason({"error": "service unavailable"}) == "unavailable"
    assert error_reason('{"success": false, "message": "missing required argument"}') == "invalid_args"
    assert api_error_class(429) == "4xx" and api_error_class("x") == "other" and api_error_class(None) == "network"
    assert route_key("https://api.example.org/v1", "/models/x.gguf") == "custom/unknown"
    assert tool_key("made up tool with words", {}) == "other"  # invented names are content
    assert tool_key("mcp__a__b", {}) == "mcp__a__b"

    roll = Rollup(tmp_path, plugin_version="0.2.0", today=Clock())
    for i in range(MAX_TOOL_KEYS + 1):
        roll.on_post_tool_call(tool_name=f"mcp__srv__tool_{i}", result="ok")
    roll.on_post_tool_call(tool_name="mcp__srv__tool_0", result="ok")  # existing keys still count
    tc = roll.counters["tool_calls"]
    assert len(tc) == MAX_TOOL_KEYS + 1
    assert tc["other"] == {"ok": 1, "error": 0}
    assert f"mcp__srv__tool_{MAX_TOOL_KEYS}" not in tc
    assert tc["mcp__srv__tool_0"] == {"ok": 2, "error": 0}


def test_restart_mid_day_keeps_counts(agent, tmp_path):
    clock = Clock()
    rec, roll, hooks = _setup(agent, tmp_path, clock)
    hooks["on_session_start"](session_id="a")
    hooks["post_tool_call"](tool_name="terminal", args={"cmd": "ls"}, result="ok", duration_ms=5)

    # Restart: new Rollup on the same directory.
    roll2 = Rollup(tmp_path / "data", plugin_version="0.2.0", today=clock)
    assert roll2.counters["sessions"]["started"] == 1
    hooks2 = ltms_recorder.wire(rec, roll2)
    hooks2["post_tool_call"](tool_name="terminal", args={}, result="ok", duration_ms=5)
    assert roll2.counters["tool_calls"]["terminal"] == {"ok": 2, "error": 0}

    # Next day, another restart: the finished day is still sent once and then removed.
    clock.day = "2026-11-23"
    roll3 = Rollup(tmp_path / "data", plugin_version="0.2.0", today=clock)
    hooks3 = ltms_recorder.wire(rec, roll3)
    rec.flush()  # the recorder's thread starts the chain
    hooks3["post_compaction"]()  # the next hook signs the finished day
    rec.flush()
    (r,) = _rollups(agent)
    assert r["payload"]["tool_calls"]["terminal"] == {"ok": 2, "error": 0}
    hooks3["post_compaction"]()
    assert not roll3.path("2026-11-22").exists()


def test_rollup_waits_while_offline_then_sends(agent, tmp_path, monkeypatch):
    clock = Clock()
    rec, roll, hooks = _setup(agent, tmp_path, clock, settings={"record_mode": "off"})
    hooks["post_compaction"]()
    clock.day = "2026-11-23"
    # Chain not started yet and hooks never use the network: nothing is signed.
    assert roll.emit_due(rec) == 0
    assert roll.path("2026-11-22").exists()
    rec.flush()  # starts the chain (online)
    hooks["post_compaction"]()  # now the rollup is signed locally
    rec.flush()
    assert len(_rollups(agent)) == 1


def test_rollup_false_disables_it(monkeypatch, tmp_path):
    from ltms_contract import generate_key, save_key
    key = tmp_path / "k.json"
    save_key(key, "hermes-reg", generate_key())
    monkeypatch.setenv("LTMS_SERVER", "http://127.0.0.1:9")
    monkeypatch.setenv("LTMS_KEY_PATH", str(key))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(ltms_recorder.atexit, "register", lambda fn: None)

    class Ctx:
        def __init__(self, config):
            self.hooks, self.config = {}, config

        def get_config(self):
            return self.config

        def register_hook(self, name, fn):
            self.hooks[name] = fn

    off = Ctx({"rollup": False})
    ltms_recorder.register(off)
    assert set(off.hooks) == set(ltms_recorder.HOOKS)
    off.hooks["post_tool_call"](tool_name="terminal", args={}, result="ok")
    assert not list((tmp_path / "hermes" / "plugin-data" / "ltms_recorder").glob("rollup-*.json"))
    off.hooks["post_tool_call"].__self__._stop.set()

    on = Ctx({})
    ltms_recorder.register(on)
    assert set(on.hooks) == set(ltms_recorder.HOOKS) | set(ltms_recorder.ROLLUP_HOOKS)
    on.hooks["post_tool_call"](tool_name="terminal", args={}, result="ok")
    assert list((tmp_path / "hermes" / "plugin-data" / "ltms_recorder").glob("rollup-*.json"))


def test_hooks_never_raise(tmp_path):
    roll = Rollup(tmp_path, plugin_version="0.2.0", today=Clock())
    roll.on_post_api_request(provider=object(), model=None, usage="garbage", api_duration="x")
    roll.on_api_request_error(status_code=object())
    roll.on_post_tool_call(tool_name=None, result=object())
    roll.on_session_end()
    assert roll.counters["model_routes"]["custom/unknown"]["calls"] == 1


def test_bridged_calls_count_under_the_real_tool(tmp_path):
    roll = Rollup(tmp_path, plugin_version="0.2.2", today=Clock())
    args = json.dumps({"calls": [{"name": "mcp__vector__vector_drive", "arguments": {"distance_mm": 50}}]})
    roll.on_post_tool_call(tool_name="tool_call", args=args, result='{"status": "ok"}')
    assert roll.counters["tool_calls"] == {"mcp__vector__vector_drive": {"ok": 1, "error": 0}}
