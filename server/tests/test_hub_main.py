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
        "SWARM": "echo-base",
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
    # SWARM is no longer required (#293): the hub looks its topic ID up from LTMS.
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
    assert client.subscribes == [("echo-base/+/records", 1), ("echo-base/+/hello", 1), ("echo-base/+/say", 1)]


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


# --- hub heartbeat (#281) ---
def test_load_config_heartbeat_defaults_and_state_path():
    cfg = load_config(base_env())
    assert cfg.heartbeat_seconds == 300.0
    assert cfg.state_path == "hub-head.json"
    cfg = load_config({**base_env(), "HUB_OUTBOX_PATH": "/var/lib/hub/hub-outbox.db"})
    assert cfg.state_path == "/var/lib/hub/hub-head.json"  # beside the outbox by default
    cfg = load_config({**base_env(), "HUB_HEARTBEAT_SECONDS": "30", "HUB_STATE_PATH": "/x/h.json"})
    assert cfg.heartbeat_seconds == 30.0
    assert cfg.state_path == "/x/h.json"


def test_load_config_bad_heartbeat():
    for bad in ("0", "-5", "soon"):
        with pytest.raises(ValueError, match="HUB_HEARTBEAT_SECONDS"):
            load_config({**base_env(), "HUB_HEARTBEAT_SECONDS": bad})


def test_heartbeat_timer_due_on_start_then_every_interval():
    from cli_agent.hub import HeartbeatTimer
    t = HeartbeatTimer(300.0, now=1000.0)
    assert t.due(1000.0) is True       # on start
    assert t.due(1001.0) is False
    assert t.due(1299.9) is False      # not before the interval
    assert t.due(1300.0) is True
    assert t.due(1300.5) is False


# --- topics by swarm topic ID (#293) ---
def test_swarm_is_optional_and_topic_settings_load():
    env = base_env()
    del env["SWARM"]
    cfg = load_config(env)
    assert (cfg.swarm, cfg.swarm_topic_id, cfg.legacy_name) == (None, None, None)
    cfg = load_config({**base_env(), "SWARM_TOPIC_ID": "sw-1a2b3c4d", "SWARM_LEGACY_NAME": "echo-base"})
    assert (cfg.swarm_topic_id, cfg.legacy_name) == ("sw-1a2b3c4d", "echo-base")
    with pytest.raises(ValueError, match="SWARM_TOPIC_ID"):
        load_config({**base_env(), "SWARM_TOPIC_ID": "bad/topic"})


def test_topic_precedence_and_legacy_prefix():
    from cli_agent.hub_main import legacy_prefix
    from cli_agent.swarm_topic import resolve
    looked_up = {"topic_id": "sw-1a2b3c4d", "name": "echo-base"}
    # SWARM_TOPIC_ID wins over the lookup and the name
    assert resolve("sw-99999999", "echo-base", lambda: looked_up) == ("sw-99999999", "config")
    # then LTMS
    assert resolve(None, "echo-base", lambda: looked_up) == ("sw-1a2b3c4d", "ltms")
    # then the deprecated name, with a warning
    assert resolve(None, "echo-base", lambda: None) == ("echo-base", "name")
    # a hub that moved off SWARM=<name> keeps serving the name while members migrate
    cfg = load_config(base_env())
    assert legacy_prefix(cfg, "ltms") == "echo-base"
    assert legacy_prefix(cfg, "name") is None
    cfg = load_config({**base_env(), "SWARM_LEGACY_NAME": "old-name"})
    assert legacy_prefix(cfg, "config") == "old-name"


def test_on_connect_subscribes_every_prefix():
    calls = []

    class Client:
        def subscribe(self, topic, qos):
            calls.append((topic, qos))

    make_on_connect(["sw-1a2b3c4d", "echo-base"])(Client(), None, None, 0, None)
    assert calls == [("sw-1a2b3c4d/+/records", 1), ("sw-1a2b3c4d/+/hello", 1), ("sw-1a2b3c4d/+/say", 1),
                     ("echo-base/+/records", 1), ("echo-base/+/hello", 1), ("echo-base/+/say", 1)]


# --- swarm messages (#300) ---
def test_max_msgs_per_second_setting():
    assert load_config(base_env()).max_msgs_per_second == 10
    assert load_config({**base_env(), "HUB_MAX_MSGS_PER_SECOND": "3"}).max_msgs_per_second == 3
    for bad in ("0", "-1", "2.5", "lots"):
        with pytest.raises(ValueError, match="HUB_MAX_MSGS_PER_SECOND"):
            load_config({**base_env(), "HUB_MAX_MSGS_PER_SECOND": bad})


def test_on_message_routes_say_to_on_say():
    seen = []

    class FakeHub:
        def on_say(self, topic, payload):
            seen.append(("say", topic))

        def on_hello(self, topic, payload):
            seen.append(("hello", topic))

        def on_record(self, topic, payload):
            seen.append(("record", topic))

    on_message = make_on_message(FakeHub())

    class Msg:
        def __init__(self, topic):
            self.topic, self.payload = topic, b"{}"
    for t in ("sw-1/a/say", "sw-1/a/hello", "sw-1/a/records"):
        on_message(None, None, Msg(t))
    assert seen == [("say", "sw-1/a/say"), ("hello", "sw-1/a/hello"), ("record", "sw-1/a/records")]
