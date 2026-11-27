"""The robot's mind inside the sidecar (#519): off unless set, dreams while resting."""
from __future__ import annotations

from types import SimpleNamespace as NS

from adapters.vector_sdk import VectorSDK
from sidecar import build_mind

ENV = {"LTMS_SERVER": "https://ltms.example.org", "LTMS_KEY_PATH": "/etc/robot-sidecar/robot.key"}


def test_off_unless_set():
    assert build_mind(ENV, object(), [VectorSDK()]) is None
    assert build_mind({**ENV, "SIDECAR_MIND_SECONDS": "0"}, object(), [VectorSDK()]) is None


def test_on_dreams_while_vector_rests_and_thinks_only_then(tmp_path):
    v = VectorSDK()
    env = {**ENV, "SIDECAR_MIND_SECONDS": "1h", "MEMBER_STATE_PATH": str(tmp_path / "head.json"),
           "MIND_BRAIN": "local", "MIND_LOCAL_URL": "http://127.0.0.1:11434/v1", "MIND_LOCAL_MODEL": "qwen2.5:1.5b"}
    mind = build_mind(env, object(), [v])
    assert mind.mind.every == 3600 and mind.dream_brain.name == "local" and mind.awake_brain.name == "none"
    assert mind.mind.cfg.state_path == str(tmp_path / "mind.json") and mind.mind.cfg.health is False
    assert mind.dream_when() is False                 # no battery reading yet
    v.on_battery(lambda item: None, NS(battery_volts=4.1, battery_level=3, is_charging=True,
                                       is_on_charger_platform=True))
    assert mind.dream_when() is True                  # on the charger: dream
