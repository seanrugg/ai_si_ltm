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
    return {"LTMS_KEY_PATH": "keys/jetson-hub.json", "SWARM": "echo-base"}


def _make_link(tmp_path, name="z3-r0"):
    key = generate_key()
    sent = []

    def publish(topic, payload, retain=False):
        sent.append((topic, payload, retain))

    outbox = Outbox(tmp_path / "out.db")
    link = MemberLink(name, key, outbox, tmp_path / "head.json", publish, "echo-base")
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
        heartbeat_seconds=43200.0,
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


def test_load_config_missing_swarm():
    env = dict(base_env())
    del env["SWARM"]
    with pytest.raises(ValueError) as exc:
        load_config(env)
    assert "SWARM" in str(exc.value)


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
    assert [topic for topic, _, _ in sent] == [records_topic("z3-r0", "echo-base")]
    assert sent[0][2] is False  # not retained


def test_make_on_message_only_enqueues(tmp_path):
    key, link, _sent, outbox = _make_link(tmp_path)
    acks: queue.Queue[bytes] = queue.Queue()
    on_message = make_on_message(acks)
    payload = b'{"last_ack_seq": 1, "last_hash": "' + ("a" * 64).encode() + b'"}'
    on_message(
        None,
        None,
        types.SimpleNamespace(topic=acks_topic("z3-r0", "echo-base"), payload=payload),
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


def test_on_connect_publishes_hello_when_given():
    class FakeClient:
        def __init__(self) -> None:
            self.subscribes, self.publishes = [], []

        def subscribe(self, topic, qos=0):
            self.subscribes.append((topic, qos))

        def publish(self, topic, payload, qos=0, retain=False):
            self.publishes.append((topic, payload, qos, retain))

    client = FakeClient()
    on_connect = make_on_connect(acks_topic("z3-r0", "echo-base"), threading.Event(),
                                 lambda: ("echo-base/z3-r0/hello", b'{"agent":"z3-r0"}'))
    on_connect(client, None, {}, 0, None)
    assert client.publishes == [("echo-base/z3-r0/hello", b'{"agent":"z3-r0"}', 1, False)]



# --- topics by swarm topic ID (#293) ---
def test_member_prefix_precedence(caplog):
    from cli_agent.member_main import member_prefix
    info = {"topic_id": "sw-1a2b3c4d", "name": "echo-base"}
    cfg = load_config({"LTMS_KEY_PATH": "k.json", "SWARM_TOPIC_ID": "sw-1a2b3c4d", "SWARM": "echo-base"})
    assert member_prefix(cfg, lambda: info) == ("sw-1a2b3c4d", "config")
    # An explicit SWARM name is kept (the broker ACL may not allow the new topics yet), with a hint.
    cfg = load_config({"LTMS_KEY_PATH": "k.json", "SWARM": "echo-base", "LTMS_SERVER": "http://x"})
    with caplog.at_level("WARNING"):
        assert member_prefix(cfg, lambda: info) == ("echo-base", "name")
    assert "SWARM_TOPIC_ID=sw-1a2b3c4d" in caplog.text
    # Neither set: looked up from LTMS.
    cfg = load_config({"LTMS_KEY_PATH": "k.json", "LTMS_SERVER": "http://x"})
    assert member_prefix(cfg, lambda: info) == ("sw-1a2b3c4d", "ltms")


def test_member_needs_some_way_to_find_its_topic():
    from cli_agent.member_main import member_prefix
    from cli_agent.swarm_topic import NoSwarmTopic
    with pytest.raises(ValueError, match="SWARM_TOPIC_ID"):
        load_config({"LTMS_KEY_PATH": "k.json"})
    cfg = load_config({"LTMS_KEY_PATH": "k.json", "LTMS_SERVER": "http://x"})
    with pytest.raises(NoSwarmTopic):
        member_prefix(cfg, lambda: None)  # a free agent, or LTMS unreachable


# --- swarm messages (#300) ---
def test_member_receives_swarm_messages_but_not_its_own(tmp_path):
    import queue
    from cli_agent.member_link import MemberLink
    from cli_agent.member_main import drain_messages, make_on_message
    from cli_agent.outbox import Outbox
    from ltms_contract import generate_key

    published = []
    other = MemberLink("vector", generate_key(), Outbox(tmp_path / "v.db"), tmp_path / "v.json",
                       lambda t, p, retain=False: published.append((t, p)), "sw-1a2b3c4d")
    other.send_message("hello swarm")
    mine = MemberLink("z3-r0", generate_key(), Outbox(tmp_path / "z.db"), tmp_path / "z.json",
                      lambda t, p, retain=False: published.append((t, p)), "sw-1a2b3c4d")
    mine.send_message("talking to myself")

    acks, messages = queue.Queue(), queue.Queue()
    on_message = make_on_message(acks, messages)

    class Msg:
        def __init__(self, topic, payload):
            self.topic, self.payload = topic, payload
    for topic, payload in published:
        on_message(None, None, Msg(topic, payload))
    on_message(None, None, Msg("sw-1a2b3c4d/z3-r0/acks", b'{"last_ack_seq": 0}'))
    on_message(None, None, Msg("sw-1a2b3c4d/rogue/say", b"not json"))
    assert acks.qsize() == 1

    got = []
    assert drain_messages(messages, "z3-r0", "sw-1a2b3c4d", got.append) == 1
    assert [r["summary"] for r in got] == ["hello swarm"] and got[0]["agent"] == "vector"


def test_send_message_validates_text(tmp_path):
    from cli_agent.member_link import MemberLink
    from cli_agent.outbox import Outbox
    from ltms_contract import generate_key
    link = MemberLink("z3-r0", generate_key(), Outbox(tmp_path / "z.db"), tmp_path / "z.json",
                      lambda *a, **k: None, "sw-1a2b3c4d")
    for bad in ("", "x" * 501):
        with pytest.raises(ValueError):
            link.send_message(bad)
    rec = link.send_message("to two", to="vector")
    assert rec["peers"] == ["vector"]


# ------------------------------------------------------------------ directives (#490)
def _directive(fleet, did="d-0001", text="Dock by 22:00"):
    import json
    from cli_agent.directives import DOMAIN

    statement = json.dumps({"id": did, "text": text, "scope": {"all": True}})
    sig = fleet.sign(DOMAIN.encode() + statement.encode()).signature.hex()
    return json.dumps({"kind": "directive", "id": did, "statement": statement, "signature": sig,
                       "signer": "fleet"}).encode()


def _fleet_queue(*payloads):
    """Fleet-topic messages through the member's own on_message, as the broker delivers them."""
    fleet = queue.Queue()
    on_message = make_on_message(queue.Queue(), queue.Queue(), threading.Event(), fleet)
    for i, payload in enumerate(payloads):
        on_message(None, None, types.SimpleNamespace(topic=f"sw-1a2b3c4d/fleet/d-{i}", payload=payload))
    return fleet


def test_member_acknowledges_a_fleet_directive(tmp_path):
    from cli_agent.agent import verify_chain
    from cli_agent.directives import DirectiveHandler
    from cli_agent.member_main import drain_directives

    fleet = generate_key()
    key, link, _sent, outbox = _make_link(tmp_path)
    handler = DirectiveHandler(link, lambda: fleet.verify_key.encode().hex(), str(tmp_path / "directives.json"))
    msgs = _fleet_queue(_directive(fleet))
    assert msgs.qsize() == 1

    assert drain_directives(msgs, handler) == 1
    recs = outbox.pending("z3-r0", 100)
    assert [r["tags"] for r in recs] == [["directive-ack"], ["directive-ack"]]
    assert [r["payload"]["status"] for r in recs] == ["received", "accepted"]
    assert all(r["payload"]["directive"] == "d-0001" for r in recs)
    ok, msg = verify_chain(recs, bytes(key.verify_key))
    assert ok, msg

    # A retained re-delivery after a reconnect isn't acknowledged twice.
    assert drain_directives(_fleet_queue(_directive(fleet)), handler) == 1
    assert len(outbox.pending("z3-r0", 100)) == 2


def test_member_declines_when_its_behaviour_says_so(tmp_path):
    from cli_agent.directives import Decision, DirectiveHandler
    from cli_agent.member_main import drain_directives

    fleet = generate_key()
    _key, link, _sent, outbox = _make_link(tmp_path)
    handler = DirectiveHandler(link, lambda: fleet.verify_key.encode().hex(), str(tmp_path / "d.json"),
                               decide=lambda stmt: Decision.decline("outside my safety envelope"))
    drain_directives(_fleet_queue(_directive(fleet)), handler)
    last = outbox.pending("z3-r0", 100)[-1]
    assert last["payload"] == {"directive": "d-0001", "status": "declined", "reason": "outside my safety envelope"}


def test_bad_directives_are_logged_and_the_member_keeps_going(tmp_path, caplog):
    import logging

    from cli_agent.directives import DirectiveHandler
    from cli_agent.member_main import drain_directives

    fleet, impostor = generate_key(), generate_key()
    _key, link, _sent, outbox = _make_link(tmp_path)
    handler = DirectiveHandler(link, lambda: fleet.verify_key.encode().hex(), str(tmp_path / "d.json"))
    msgs = _fleet_queue(_directive(impostor, did="d-bad"), b"not json", _directive(fleet, did="d-good"))
    with caplog.at_level(logging.INFO):
        assert drain_directives(msgs, handler) == 3
    statuses = [(r["tags"], (r.get("payload") or {}).get("status")) for r in outbox.pending("z3-r0", 100)]
    assert statuses == [(["directive-invalid"], None), (["directive-ack"], "received"),
                        (["directive-ack"], "accepted")]
    assert "directive message: invalid" in caplog.text and "directive message: unreadable" in caplog.text

    class Broken:
        def on_message(self, payload):
            raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        assert drain_directives(_fleet_queue(b"{}", b"{}"), Broken()) == 2  # both tried, none fatal
    assert "directive handling failed" in caplog.text


# ------------------------------------------------------------------ the embeddable member (#491)
class FakeMqtt:
    """Enough of paho's client for MemberNode: records publishes and subscriptions."""

    def __init__(self, name):
        self.name, self.published, self.subscribed = name, [], []
        self.started = self.stopped = self.disconnected = False

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def username_pw_set(self, user, password):
        self.user = user

    def connect_async(self, host, port):
        self.host = (host, port)

    def loop_start(self):
        self.started = True

    def loop_stop(self):
        self.stopped = True

    def disconnect(self):
        self.disconnected = True

    # what the broker does
    def connected(self):
        self.on_connect(self, None, None, 0, None)

    def deliver(self, topic, payload):
        self.on_message(self, None, types.SimpleNamespace(topic=topic, payload=payload))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _node(tmp_path, name="z3-r0", **kw):
    from cli_agent.member_main import MemberNode
    from ltms_contract import save_key

    key = generate_key()
    save_key(str(tmp_path / f"{name}.key.json"), name, key)
    cfg = load_config({"LTMS_KEY_PATH": str(tmp_path / f"{name}.key.json"), "SWARM_TOPIC_ID": "sw-1a2b3c4d",
                       "MEMBER_OUTBOX_PATH": str(tmp_path / "out.db"),
                       "MEMBER_STATE_PATH": str(tmp_path / "head.json"), "HEARTBEAT_SECONDS": "60",
                       "TRANSPORT": "mqtt"})
    clock = Clock()
    mqtts = []

    def factory(client_id):
        mqtts.append(FakeMqtt(client_id))
        return mqtts[-1]

    node = MemberNode(cfg, mqtt_factory=factory, clock=clock, **kw)
    return node, key, clock, mqtts


def test_a_host_drives_the_member_without_main(tmp_path):
    import json

    from cli_agent.agent import verify_chain

    node, key, clock, mqtts = _node(tmp_path)
    assert node.status() == {"running": False}
    node.start()
    mq = mqtts[0]
    assert mq.started and mq.host == ("localhost", 1883)
    mq.connected()
    assert "sw-1a2b3c4d/z3-r0/acks" in mq.subscribed and node.status()["mqtt"] is True

    node.step()  # nothing due yet
    assert node.link.head["seq"] == 0
    clock.t += 61
    node.step()  # heartbeat due
    recs = node.outbox.pending("z3-r0", 10)
    assert [r["summary"] for r in recs] == ["heartbeat"]
    assert any(t == "sw-1a2b3c4d/z3-r0/records" for t, _ in mq.published)
    ok, msg = verify_chain(recs, bytes(key.verify_key))
    assert ok, msg

    # the hub acks it
    mq.deliver("sw-1a2b3c4d/z3-r0/acks", json.dumps({"last_ack_seq": 1, "last_hash": recs[0]["hash"]}).encode())
    node.step()
    st = node.status()
    assert st["head_seq"] == 1 and st["unacked"] == 0 and st["prefix"] == "sw-1a2b3c4d"

    node.stop()
    assert mq.disconnected and mq.stopped and node.status() == {"running": False}
    node.stop()  # twice is harmless
    with pytest.raises(RuntimeError):
        node.step()


def test_on_message_sees_swarm_messages(tmp_path):
    from cli_agent.member_link import MemberLink
    from cli_agent.outbox import Outbox

    seen = []
    node, _key, _clock, mqtts = _node(tmp_path, on_message=seen.append)
    node.start()
    published = []
    vector = MemberLink("vector", generate_key(), Outbox(tmp_path / "v.db"), tmp_path / "v.json",
                        lambda topic, payload, retain=False: published.append((topic, payload)), "sw-1a2b3c4d")
    vector.send_message("hello z3", to="z3-r0")
    for topic, payload in published:
        mqtts[0].deliver(topic, payload)
    node.step()
    assert [r["summary"] for r in seen] == ["hello z3"] and seen[0]["agent"] == "vector"
    node.stop()


def test_on_message_errors_are_logged_not_fatal(tmp_path, caplog):
    import logging

    from cli_agent.member_link import MemberLink
    from cli_agent.outbox import Outbox

    def boom(rec):
        raise RuntimeError("behaviour bug")

    node, _key, clock, mqtts = _node(tmp_path, on_message=boom)
    node.start()
    published = []
    MemberLink("vector", generate_key(), Outbox(tmp_path / "v.db"), tmp_path / "v.json",
               lambda t, p, retain=False: published.append((t, p)), "sw-1a2b3c4d").send_message("hi")
    mqtts[0].deliver(*published[0])
    with caplog.at_level(logging.ERROR):
        node.step()
    assert "on_message failed" in caplog.text
    clock.t += 61
    node.step()  # still running: the heartbeat goes out
    assert node.link.head["seq"] == 1
    node.stop()


def test_the_running_member_follows_and_declines_directives(tmp_path):
    from cli_agent.directives import Decision

    fleet = generate_key()
    env = {"FLEET_PUBLIC_KEY": fleet.verify_key.encode().hex()}
    node, _key, _clock, mqtts = _node(tmp_path, env=env,
                                      decide=lambda stmt: Decision.decline("outside my safety envelope")
                                      if "drive" in stmt["text"] else Decision(True))
    node.start()
    mqtts[0].connected()
    assert "sw-1a2b3c4d/fleet/+" in mqtts[0].subscribed
    mqtts[0].deliver("sw-1a2b3c4d/fleet/d-1", _directive(fleet, did="d-1", text="Dock by 22:00"))
    mqtts[0].deliver("sw-1a2b3c4d/fleet/d-2", _directive(fleet, did="d-2", text="drive to the door"))
    node.step()
    acks = [(r["payload"]["directive"], r["payload"]["status"], r["payload"].get("reason"))
            for r in node.outbox.pending("z3-r0", 10)]
    assert acks == [("d-1", "received", None), ("d-1", "accepted", None),
                    ("d-2", "received", None), ("d-2", "declined", "outside my safety envelope")]
    node.stop()


def test_submit_from_another_thread_signs_into_the_chain(tmp_path):
    from cli_agent.agent import verify_chain

    node, key, clock, _mqtts = _node(tmp_path)
    node.start()
    results = []

    def worker():
        fut = node.submit(lambda link: link.append("thought", summary="from a worker thread"))
        results.append(fut.result(timeout=5))

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.monotonic() + 5
    while t.is_alive() and time.monotonic() < deadline:
        node.step()  # the host's loop
        time.sleep(0.01)
    t.join(1)
    assert results and results[0]["summary"] == "from a worker thread"
    clock.t += 61
    node.step()
    recs = node.outbox.pending("z3-r0", 10)
    assert [r["summary"] for r in recs] == ["from a worker thread", "heartbeat"]
    ok, msg = verify_chain(recs, bytes(key.verify_key))
    assert ok, msg

    failing = node.submit(lambda link: 1 / 0)
    node.step()
    with pytest.raises(ZeroDivisionError):
        failing.result(timeout=1)
    node.stop()


def test_submit_before_start_or_after_stop_fails_cleanly(tmp_path):
    node, _key, _clock, _mqtts = _node(tmp_path)
    with pytest.raises(RuntimeError, match="isn't running"):
        node.submit(lambda link: link.append("event")).result(timeout=1)
    node.start()
    node.stop()
    with pytest.raises(RuntimeError, match="isn't running"):
        node.submit(lambda link: link.append("event")).result(timeout=1)


def test_main_runs_the_node(monkeypatch, tmp_path):
    """main() is MemberNode in a loop: start, step until a signal, stop (#491)."""
    from cli_agent import member_main

    calls = []

    class Node:
        def __init__(self, cfg, poll=None):
            calls.append(("init", poll))

        def start(self):
            calls.append("start")

        def step(self):
            calls.append("step")
            import signal as sig
            member_main.signal.getsignal(sig.SIGTERM)(sig.SIGTERM, None)  # ask main to stop

        def stop(self):
            calls.append("stop")

    from ltms_contract import save_key
    save_key(str(tmp_path / "z.key.json"), "z3-r0", generate_key())
    monkeypatch.setenv("LTMS_KEY_PATH", str(tmp_path / "z.key.json"))
    monkeypatch.setenv("SWARM_TOPIC_ID", "sw-1a2b3c4d")
    monkeypatch.setattr(member_main, "MemberNode", Node)
    import signal as sig
    old = sig.getsignal(sig.SIGTERM), sig.getsignal(sig.SIGINT)
    try:
        poll = object()
        member_main.main(poll=poll)
    finally:
        sig.signal(sig.SIGTERM, old[0])
        sig.signal(sig.SIGINT, old[1])
    assert calls == [("init", poll), "start", "step", "stop"]
