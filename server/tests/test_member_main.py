"""Member entrypoint: config, heartbeat and ack draining. No broker, no paho."""
from __future__ import annotations

import queue
import threading
import types
import time

import pytest

from ltms_contract import generate_key

from cli_agent.member_link import MemberLink
from cli_agent.member_main import (
    MemberConfig,
    drain_acks,
    heartbeat,
    load_config,
    make_on_connect,
    make_on_message,
)
from cli_agent.mqtt_messages import acks_topic, encode_ack, records_topic
from cli_agent.outbox import Outbox


def base_env() -> dict[str, str]:
    return {"LTMS_KEY_PATH": "keys/jetson-hub.json"}


def _make_link(tmp_path, name="z3-r0"):
    key = generate_key()
    sent = []

    def publish(topic, payload, retain=False):
        sent.append((topic, payload, retain))

    outbox = Outbox(tmp_path / "out.db")
    link = MemberLink(name, key, outbox, tmp_path / "head.json", publish)
    return key, link, sent, outbox


def test_load_config_defaults():
    cfg = load_config(base_env())
    assert cfg == MemberConfig(
        key_path="keys/jetson-hub.json",
        outbox_path="member-outbox.db",
        state_path="member-head.json",
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_username=None,
        mqtt_password=None,
        swarm="echo-base",
        heartbeat_seconds=60.0,
    )


def test_load_config_overrides():
    cfg = load_config(
        {
            **base_env(),
            "MEMBER_OUTBOX_PATH": "/tmp/member-outbox.db",
            "MEMBER_STATE_PATH": "/tmp/member-head.json",
            "MQTT_HOST": "broker.local",
            "MQTT_PORT": "1884",
            "MQTT_USERNAME": "pi-zero",
            "MQTT_PASSWORD": "s3cret",
            "SWARM": "rogue-squadron",
            "HEARTBEAT_SECONDS": "5",
        }
    )
    assert cfg.outbox_path == "/tmp/member-outbox.db"
    assert cfg.state_path == "/tmp/member-head.json"
    assert cfg.mqtt_host == "broker.local"
    assert cfg.mqtt_port == 1884
    assert cfg.mqtt_username == "pi-zero"
    assert cfg.mqtt_password == "s3cret"
    assert cfg.swarm == "rogue-squadron"
    assert cfg.heartbeat_seconds == 5.0


def test_load_config_missing_key_path():
    env = dict(base_env())
    del env["LTMS_KEY_PATH"]
    with pytest.raises(ValueError) as exc:
        load_config(env)
    assert "LTMS_KEY_PATH" in str(exc.value)


def test_load_config_bad_heartbeat():
    with pytest.raises(ValueError):
        load_config(dict(base_env(), HEARTBEAT_SECONDS="-1"))
    with pytest.raises(ValueError):
        load_config(dict(base_env(), HEARTBEAT_SECONDS="x"))


def test_load_config_error_hides_password():
    with pytest.raises(ValueError) as exc:
        load_config(dict(base_env(), HEARTBEAT_SECONDS="not-a-number", MQTT_PASSWORD="s3cret"))
    assert "s3cret" not in str(exc.value)


def test_heartbeat_appends_and_publishes(tmp_path):
    key, link, sent, _outbox = _make_link(tmp_path)

    started = time.monotonic()
    rec = heartbeat(link, started, started + 10.5)

    assert rec["type"] == "perception"
    assert rec["summary"] == "heartbeat"
    assert rec["tags"] == ["telemetry", "internal"]
    assert rec["payload"] == {"uptime_s": 10.5}
    assert [topic for topic, _, _ in sent] == [records_topic("z3-r0")]
    assert sent[0][2] is False  # not retained


def test_make_on_message_only_enqueues(tmp_path):
    key, link, _sent, outbox = _make_link(tmp_path)
    acks: queue.Queue[bytes] = queue.Queue()
    on_message = make_on_message(acks)
    payload = b'{"last_ack_seq": 1, "last_hash": "' + ("a" * 64).encode() + b'"}'
    on_message(
        None,
        None,
        types.SimpleNamespace(topic=acks_topic("z3-r0"), payload=payload),
    )
    assert acks.get_nowait() == payload
    # The link's outbox is main-thread only; on_message never touched it.
    assert outbox.pending("z3-r0") == []


def test_drain_acks_prunes_and_skips_garbage(tmp_path):
    key, link, _sent, outbox = _make_link(tmp_path)
    for _ in range(3):  # seqs 1, 2, 3
        heartbeat(link, 0.0, 0.0)
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [1, 2, 3]

    acks: queue.Queue[bytes] = queue.Queue()
    acks.put(encode_ack({"last_ack_seq": 2, "last_hash": "b" * 64}))
    acks.put(b"garbage")

    pruned = drain_acks(link, acks)
    assert pruned == 2
    assert acks.empty()
    # seq 1 and 2 are pruned; seq 3 is still waiting.
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [3]


def test_make_on_connect_subscribes_and_sets_event():
    class FakeClient:
        def __init__(self) -> None:
            self.subscribes = []

        def subscribe(self, topic, qos=0):
            self.subscribes.append((topic, qos))

    wanted = threading.Event()
    client = FakeClient()
    on_connect = make_on_connect(acks_topic("z3-r0", "echo-base"), wanted)
    assert not wanted.is_set()
    on_connect(client, None, {}, 0, None)
    assert wanted.is_set()
    assert client.subscribes == [("echo-base/z3-r0/acks", 1)]
