"""Hermes tool_guard plugin: refuse out-of-range tool arguments before the tool runs."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations" / "hermes"))

import tool_guard  # noqa: E402
from tool_guard import Guard, check, limits_for, resolve_limits  # noqa: E402

LIMITS = resolve_limits(None)  # the defaults


class Ctx:
    def __init__(self, config=None):
        self.hooks, self.config = {}, config

    def get_config(self):
        return self.config

    def register_hook(self, name, fn):
        self.hooks[name] = fn


@pytest.mark.parametrize("args", [
    {"distance_mm": 100}, {"distance_mm": -100}, {"angle_deg": 90}, {"angle_deg": -45, "speed": 30},
    {"distance_mm": 50, "speed": 100}, {}, {"distance_mm": None, "angle_deg": 10}, {"distance_mm": "80"},
])
def test_in_range_calls_run(args):
    assert check("mcp__vector__vector_drive", args, LIMITS) is None


@pytest.mark.parametrize("args, words", [
    ({"distance_mm": 2000}, "distance_mm=2000 is outside the limit of ±100"),
    ({"distance_mm": -101}, "distance_mm=-101"),
    ({"angle_deg": 360}, "angle_deg=360"),
    ({"distance_mm": 50, "speed": 500}, "speed=500"),
    ({"distance_mm": "far"}, "is not a number"),
    ({"distance_mm": float("inf")}, "not a finite number"),
    ({"distance_mm": float("nan")}, "not a finite number"),
    ({"distance_mm": True}, "is not a number"),
    ("drive 2 metres", "not an object"),
])
def test_out_of_range_calls_are_refused(args, words):
    reason = check("mcp__vector__vector_drive", args, LIMITS)
    assert reason and words in reason


def test_bare_name_matches_any_mcp_server_and_other_tools_pass():
    assert limits_for("vector_drive", LIMITS)
    assert limits_for("mcp__vector__vector_drive", LIMITS)
    assert limits_for("mcp__vector-routine__vector_drive", LIMITS)
    assert limits_for("mcp__vector__vector_say", LIMITS) is None
    assert limits_for("terminal", LIMITS) is None
    assert check("terminal", {"distance_mm": 99999}, LIMITS) is None


def test_block_directive_shape_and_count(caplog):
    guard = Guard(LIMITS)
    assert guard.on_pre_tool_call(tool_name="mcp__vector__vector_say", args={"text": "hi"}) is None
    assert guard.on_pre_tool_call(tool_name="mcp__vector__vector_drive", args={"distance_mm": 40},
                                  session_id="s", task_id="t", tool_call_id="c") is None
    with caplog.at_level(logging.WARNING, logger="tool_guard"):
        out = guard.on_pre_tool_call(tool_name="mcp__vector__vector_drive", args={"distance_mm": 2000})
    assert out["action"] == "block"
    assert out["message"].startswith("Error: refused by tool_guard")  # the recorder marks it failed
    assert "did not move" in out["message"]
    assert guard.blocked == 1
    assert "blocked mcp__vector__vector_drive" in caplog.text


def test_settings_replace_defaults_and_bad_entries_are_skipped(caplog):
    limits = resolve_limits({"vector_drive": {"distance_mm": 300}, "arm_move": {"joint_deg": 45, "x": "big"},
                             "bad": 5, "": {"a": 1}, "neg": {"a": -1}})
    assert limits == {"vector_drive": {"distance_mm": 300.0}, "arm_move": {"joint_deg": 45.0}}
    assert check("vector_drive", {"angle_deg": 720}, limits) is None  # angle no longer limited
    assert check("vector_drive", {"distance_mm": 250}, limits) is None
    assert resolve_limits({}) == {}


def test_register_wires_pre_tool_call_with_defaults():
    ctx = Ctx({})
    tool_guard.register(ctx)
    assert set(ctx.hooks) == {"pre_tool_call"}
    assert ctx.hooks["pre_tool_call"](tool_name="vector_drive", args={"distance_mm": 500})["action"] == "block"


def test_register_with_empty_limits_registers_nothing():
    ctx = Ctx({"limits": {}})
    tool_guard.register(ctx)
    assert ctx.hooks == {}


def test_register_never_raises():
    class Broken:
        def get_config(self):
            raise RuntimeError("boom")

        def register_hook(self, *a):
            raise RuntimeError("boom")

    tool_guard.register(Broken())  # no exception


def test_internal_error_fails_closed_only_for_guarded_tools(monkeypatch):
    guard = Guard(LIMITS)
    monkeypatch.setattr(tool_guard, "check", lambda *a: 1 / 0)
    assert guard.on_pre_tool_call(tool_name="vector_drive", args={})["action"] == "block"
    assert guard.on_pre_tool_call(tool_name="terminal", args={}) is None
