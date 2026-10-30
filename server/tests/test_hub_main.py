"""Hub entrypoint: config and MQTT wiring factories. No broker, no paho."""
from __future__ import annotations

import types

import pytest

from cli_agent.hub_main import (
    HubConfig,
    load_config,
    make_on_connect,
    make_on_message,
    make_publish,
    records_subscription,
)


def base_env() -> dict[str, str]:
    return {
        "LTMS_SERVER": "http://localhost:8080",
        "LTMS_KEY_PATH": "keys/jetson-hub.json",
    }


def test_load_config_defaults():
    cfg = load_config(base_env())
    assert cfg == HubConfig(
        ltms_server="http://localhost:8080",
        key_path="keys/jetson-hub.json",
        outbox_path="hub-outbox.db",
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_username=None,
        mqtt_password=None,
        swarm="echo-base",
        tick_seconds=10.0,
    )


def test_load_config_overrides():
    cfg = load_config(
        {
            **base_env(),
            "HUB_OUTBOX_PATH": "/tmp/hub-outbox.db",
            "MQTT_HOST": "broker.local",
            "MQTT_PORT": "1884",
            "MQTT_USERNAME": "jetson",
            "MQTT_PASSWORD": "s3cret",
            "SWARM": "rogue-squadron",
            "TICK_SECONDS": "2.5",
        }
    )
    assert cfg.outbox_path == "/tmp/hub-outbox.db"
    assert cfg.mqtt_host == "broker.local"
    assert cfg.mqtt_port == 1884
    assert cfg.mqtt_username == "jetson"
    assert cfg.mqtt_password == "s3cret"
    assert cfg.swarm == "rogue-squadron"
    assert cfg.tick_seconds == 2.5


def test_load_config_missing_required():
    for missing in ("LTMS_SERVER", "LTMS_KEY_PATH"):
        env = base_env()
        del env[missing]
        with pytest.raises(ValueError) as exc:
            load_config(env)
        assert missing in str(exc.value), f"expected {missing} named in {exc.value!r}"


def test_load_config_bad_port_and_tick():
    env = dict(base_env(), MQTT_PORT="x")
    with pytest.raises(ValueError):
        load_config(env)
    env = dict(base_env(), TICK_SECONDS="0")
    with pytest.raises(ValueError):
        load_config(env)
    env = dict(base_env(), TICK_SECONDS="-3")
    with pytest.raises(ValueError):
        load_config(env)
    env = dict(base_env(), TICK_SECONDS="x")
    with pytest.raises(ValueError):
        load_config(env)


def test_load_config_error_hides_password():
    env = dict(base_env(), MQTT_PORT="not-a-port", MQTT_PASSWORD="s3cret")
    with pytest.raises(ValueError) as exc:
        load_config(env)
    assert "s3cret" not in str(exc.value)


def test_records_subscription():
    assert records_subscription("echo-base") == "echo-base/+/records"
    assert records_subscription("rogue-squadron") == "rogue-squadron/+/records"


class FakeHub:
    def __init__(self) -> None:
        self.calls = []
        self.raise_on = False

    def on_record(self, topic, payload):
        self.calls.append((topic, payload))
        if self.raise_on:
            raise RuntimeError("boom")


def test_make_on_message_passes_through():
    hub = FakeHub()
    on_message = make_on_message(hub)
    msg = types.SimpleNamespace(
        topic="echo-base/z3-r0/records", payload=b'{"seq": 1}'
    )
    on_message(None, None, msg)
    assert hub.calls == [("echo-base/z3-r0/records", b'{"seq": 1}')]


def test_make_on_message_never_raises():
    hub = FakeHub()
    hub.raise_on = True
    on_message = make_on_message(hub)
    msg = types.SimpleNamespace(topic="echo-base/z3-r0/records", payload=b"x")
    on_message(None, None, msg)  # must not propagate
    assert len(hub.calls) == 1


class FakeMqttClient:
    def __init__(self) -> None:
        self.publishes = []
        self.subscribes = []
        self.username_pw_set_calls = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.publishes.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        self.subscribes.append((topic, qos))

    def username_pw_set(self, username, password=None):
        self.username_pw_set_calls.append((username, password))


def test_make_on_connect_subscribes_with_qos_one():
    client = FakeMqttClient()
    on_connect = make_on_connect("echo-base")
    on_connect(client, None, {}, 0, None)
    assert client.subscribes == [("echo-base/+/records", 1)]


def test_make_publish_always_uses_qos_one():
    client = FakeMqttClient()
    publish = make_publish(client)

    publish("echo-base/z3-r0/acks", b'{"last_ack_seq": 1}', retain=True)
    assert client.publishes == [
        ("echo-base/z3-r0/acks", b'{"last_ack_seq": 1}', 1, True)
    ]

    publish("echo-base/z3-r0/acks", b'{"last_ack_seq": 2}')
    assert client.publishes[-1] == (
        "echo-base/z3-r0/acks",
        b'{"last_ack_seq": 2}',
        1,
        False,
    )
