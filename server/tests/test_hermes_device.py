"""Hermes device mode (#521): Hermes records as M4-C2 and runs its member while Hermes runs."""
from __future__ import annotations

import contextlib
import sys
import threading
from pathlib import Path

import pytest

from cli_agent.agent import verify_chain
from cli_agent.handoff import Handoff
from ltms_contract import generate_key, save_key
from ltms_mind.member import AlreadyHosted, DeviceMember

from .test_member_main import _make_link

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations" / "hermes"))

import ltms_recorder
from ltms_recorder import device as dev


def device_env(tmp_path, **extra):
    d = tmp_path / "device"
    d.mkdir(exist_ok=True)
    save_key(d / "m4-c2.key.json", "m4-c2", generate_key())
    env = {"LTMS_SERVER": "https://ltms.example.org", "LTMS_KEY_PATH": str(d / "m4-c2.key.json"),
           "TRANSPORT": "direct", "MEMBER_OUTBOX_PATH": str(d / "member-outbox.db"),
           "MEMBER_STATE_PATH": str(d / "member-head.json"), "MEMBER_HANDOFF_PATH": str(d / "handoff.db"),
           "MEMBER_MIND_SECONDS": "30m", "MIND_BRAIN": "local",
           "MIND_LOCAL_URL": "http://127.0.0.1:11434/v1", "MIND_LOCAL_MODEL": "gemma4:31b", **extra}
    (d / "device.env").write_text(
        "# the Mac Studio\n" + "".join(f"{k}={v}\n" for k, v in env.items()) + "MIND_DREAM=always  # every wake\n")
    return env, d / "device.env"


# ------------------------------------------------------------------ reading settings
def test_env_file_and_hermes_model(tmp_path):
    env, path = device_env(tmp_path)
    got = dev.load_env_file(path)
    assert got["MEMBER_HANDOFF_PATH"] == env["MEMBER_HANDOFF_PATH"] and got["MIND_DREAM"] == "always"
    (tmp_path / "q.env").write_text("export A='x y'\nB=\"z\"\nbad line\n1X=no\n")
    assert dev.load_env_file(tmp_path / "q.env") == {"A": "x y", "B": "z"}

    home = tmp_path / "hermes"
    home.mkdir()
    assert dev.hermes_model(home) is None
    (home / "config.yaml").write_text("model:\n  provider: custom\n  default: gemma4:31b\nother: 1\n")
    assert dev.hermes_model(home) == "gemma4:31b"
    (home / "config.yaml").write_text("model: qwen3.5:122b\n")
    assert dev.hermes_model(home) == "qwen3.5:122b"


# ------------------------------------------------------------------ Hermes's records go through the handoff
def test_hermes_records_go_to_the_handoff_and_the_member_signs_them(tmp_path):
    env, _ = device_env(tmp_path)
    rec = dev.make_handoff_recorder(Handoff(env["MEMBER_HANDOFF_PATH"]), "m4-c2", {"record_mode": "summary"},
                                    background=False)
    rec.on_session_start(session_id="s1", model="gemma4:31b", platform="desktop")
    rec.on_post_tool_call(tool_name="terminal", args={"cmd": "ls"}, result="ok", duration_ms=12)
    assert rec.queued == 2
    rec.on_session_end(session_id="s1", completed=True)    # session end sends: into the handoff here
    rec.flush()
    assert rec.queued == 0 and Handoff(env["MEMBER_HANDOFF_PATH"]).pending() == 3

    key, link, _, outbox = _make_link(tmp_path, name="m4-c2")
    assert Handoff(env["MEMBER_HANDOFF_PATH"]).pump(link) == 3       # the member process signs them
    recs = outbox.pending("m4-c2", 10)
    assert [r["summary"].split(":")[0] for r in recs] == [
        "Hermes session started", "terminal ok (12 ms)", "Hermes session completed"]
    assert recs[0]["payload"]["model"] == "gemma4:31b" and "hermes" in recs[0]["tags"]
    ok, msg = verify_chain(recs, bytes(key.verify_key))
    assert ok, msg


def test_the_rollup_works_through_the_handoff(tmp_path, monkeypatch):
    from ltms_recorder.rollup import Rollup
    env, _ = device_env(tmp_path)
    h = Handoff(env["MEMBER_HANDOFF_PATH"])
    rec = dev.make_handoff_recorder(h, "m4-c2", {}, background=False)
    roll = Rollup(tmp_path / "rollup", plugin_version="t")
    monkeypatch.setattr(roll, "_past_days", lambda: ["2026-11-27"])
    roll.path("2026-11-27").parent.mkdir(parents=True, exist_ok=True)
    roll.path("2026-11-27").write_text('{"sessions": {"started": 2}}')
    roll._backlog = True
    assert roll.emit_due(rec) == 1 and h.pending() == 1
    roll._backlog = True
    assert roll.emit_due(rec) == 0 and not roll.path("2026-11-27").exists()   # settled, file removed


# ------------------------------------------------------------------ one host per device
class FakeNode:
    def __init__(self, cfg, poll):
        self.cfg, self.poll, self.steps, self.stopped, self.link = cfg, poll, 0, False, object()

    def start(self):
        pass

    def step(self):  # like MemberNode.step: a poll's error is logged, never fatal
        self.steps += 1
        with contextlib.suppress(Exception):
            self.poll(self.link, self.steps)

    def stop(self):
        self.stopped = True


def wait_for(cond, timeout=5.0):
    done = threading.Event()
    while not cond():
        assert not done.wait(0.02) and (timeout := timeout - 0.02) > 0, "timed out"


class FakeClient:
    def __init__(self, env):
        self.specs = []

    def report_specs(self, sheet):
        self.specs.append(sheet)
        return sheet


def make_device(env, model=None, nodes=None, served=lambda url: {"gemma4:31b", "qwen3.5:122b", "glm-4.7-flash:latest"}):
    env = {**env, "MEMBER_MIND_SECONDS": "100h"}      # the mind is built and switched; it doesn't wake in a test
    nodes = nodes if nodes is not None else []

    def node(cfg, poll):
        nodes.append(FakeNode(cfg, poll))
        return nodes[-1]
    return DeviceMember(env, model, client_factory=FakeClient, node_factory=node, served=served), nodes


def test_one_process_per_device_hosts_the_member(tmp_path):
    env, _ = device_env(tmp_path)
    first, nodes = make_device(env)
    first.start()
    assert first.ready.wait(5)
    second, _ = make_device(env)
    with pytest.raises(AlreadyHosted):
        second.start()                                   # a second Hermes only hands off
    first.stop()
    assert nodes[0].stopped
    third, _ = make_device(env)
    third.start()                                        # the lock is free once Hermes exits
    third.stop()


def test_the_mind_follows_hermes_s_model(tmp_path):
    env, _ = device_env(tmp_path)
    d, _nodes = make_device(env, model="gemma4:31b")
    d.start()
    assert d.ready.wait(5)
    wait_for(lambda: d.client.specs)                              # reported at start
    assert d.mind.dream_brain.model == "gemma4:31b" and d.mind.mind.cfg.model_family == "gemma"
    assert d.set_model("qwen3.5:122b") is True
    assert d.mind.dream_brain.model == "qwen3.5:122b" and d.mind.mind.cfg.model_family == "qwen"
    assert d.set_model("qwen3.5:122b") is False                  # unchanged
    assert d.set_model("claude-opus-5-5") is False               # not on the model server: keep qwen
    assert d.mind.dream_brain.model == "qwen3.5:122b"
    wait_for(lambda: len(d.client.specs) == 2)                    # and again on the change
    assert [sh["models"][0]["name"] for sh in d.client.specs] == ["gemma4:31b", "qwen3.5:122b"]
    d.stop()


# ------------------------------------------------------------------ the plugin's entry point
class _Ctx:
    def __init__(self):
        self.hooks = {}

    def get_config(self):
        return {"rollup": False}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def test_register_in_device_mode(tmp_path, monkeypatch):
    env, path = device_env(tmp_path)
    monkeypatch.setenv("LTMS_DEVICE_ENV", str(path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / "hermes").mkdir()
    (tmp_path / "hermes" / "config.yaml").write_text("model:\n  default: gemma4:31b\n")
    closers, made = [], []
    monkeypatch.setattr(ltms_recorder.atexit, "register", closers.append)

    class Recording:
        def __init__(self, env, model):
            self.env, self.model, self.models, self.stopped = env, model, [], False
            made.append(self)

        def start(self):
            pass

        def set_model(self, model):
            self.models.append(model)

        def stop(self):
            self.stopped = True

    real = dev.start_device
    monkeypatch.setattr(dev, "start_device", lambda settings: real(settings, device_factory=Recording, background=False))
    ctx = _Ctx()
    ltms_recorder.register(ctx)
    assert set(ctx.hooks) == {"on_session_start", "post_tool_call", "on_session_end"}
    assert made[0].model == "gemma4:31b" and made[0].env["MEMBER_HANDOFF_PATH"] == env["MEMBER_HANDOFF_PATH"]
    ctx.hooks["on_session_start"](session_id="s2", model="glm-4.7-flash:latest", platform="desktop")
    assert made[0].models == ["glm-4.7-flash:latest"]
    closers[0]()                                          # Hermes exits
    assert made[0].stopped and Handoff(env["MEMBER_HANDOFF_PATH"]).pending() == 1


def test_device_mode_without_settings_does_nothing(tmp_path, monkeypatch):
    (tmp_path / "empty.env").write_text("# nothing\n")
    monkeypatch.setenv("LTMS_DEVICE_ENV", str(tmp_path / "empty.env"))
    ctx = _Ctx()
    ltms_recorder.register(ctx)                           # must not raise, must not record
    assert ctx.hooks == {}
