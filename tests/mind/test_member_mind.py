"""A device member with a mind (#521): the Mac Studio's process."""
from __future__ import annotations

import pytest

from ltms_mind.config import family_of, load
from ltms_mind.member import build_mind, spec_sheet

ENV = {"LTMS_SERVER": "https://ltms.example.org", "LTMS_KEY_PATH": "/m/m4-c5.key.json"}


@pytest.mark.parametrize("model,family", [
    ("gemma4:31b", "gemma"), ("qwen2.5:1.5b", "qwen"), ("hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M", "qwen"),
    ("llama3.3:70b", "llama"), ("codellama:7b", "llama"), ("glm-4.7-flash", "glm"), ("gpt-oss:120b", "gpt-oss"),
    ("mixtral:8x7b", "mistral"), ("nemotron-3-nano", "nemotron"), ("dolphin-llama3", "llama"),
    ("dolphin3", None), ("hermes3:8b", None), (None, None), ("", None),
])
def test_family_of(model, family):
    assert family_of(model) == family


def test_the_family_comes_from_the_model_unless_set():
    local = {**ENV, "MIND_BRAIN": "local", "MIND_LOCAL_MODEL": "gemma4:31b"}
    assert load(local).model_family == "gemma"
    assert load({**local, "MIND_MODEL_FAMILY": "Gemma-Custom"}).model_family == "gemma-custom"
    assert load({**ENV, "MIND_BRAIN": "ollama", "MIND_LOCAL_MODEL": "qwen2.5:1.5b"}).model_family == "qwen"
    assert load({**ENV, "MIND_LOCAL_MODEL": "gemma4:31b"}).model_family is None      # no brain, no family


def test_off_unless_set(tmp_path):
    assert build_mind({**ENV, "MEMBER_STATE_PATH": str(tmp_path / "head.json")}, object()) is None


def test_on_the_mac_studio(tmp_path):
    env = {**ENV, "MEMBER_MIND_SECONDS": "30m", "MEMBER_STATE_PATH": str(tmp_path / "head.json"),
           "MEMBER_OUTBOX_PATH": str(tmp_path / "out.db"), "MIND_BRAIN": "local",
           "MIND_LOCAL_URL": "http://127.0.0.1:11434/v1", "MIND_LOCAL_MODEL": "gemma4:31b"}
    mind = build_mind(env, object())
    assert mind.mind.every == 1800 and mind.dream_brain.name == "local" and mind.awake_brain is mind.dream_brain
    assert mind.mind.cfg.state_path == str(tmp_path / "mind.json") and mind.mind.cfg.model_family == "gemma"
    sheet = spec_sheet(env, mind)
    assert sheet["models"] == [{"name": "gemma4:31b", "runtime": "ollama", "where": "onboard", "role": "mind"}]
    assert sheet["software"]["harness"] == "ltms-member"
    assert "models" not in spec_sheet(env, None)

