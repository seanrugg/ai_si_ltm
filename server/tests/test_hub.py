"""Hub core: MQTT records in, replicator tick, acks out."""
from __future__ import annotations

import threading

import httpx
import pytest

from cli_agent.hub import Hub
from cli_agent.mqtt_messages import decode_ack, encode_record
from cli_agent.outbox import Outbox

from server.tests.test_swarms import Home, sign


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


@pytest.fixture
def outbox(tmp_path):
    return Outbox(tmp_path / "outbox.sqlite")


def _setup(mesh, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    return hub, z3


def test_on_record_accepts_and_rejects(mesh, home, outbox):
    hub_client, z3 = _setup(mesh, home)
    sent = []
    publish = lambda t, p, retain=False: sent.append((t, p, retain))
    hub = Hub(hub_client, outbox, publish, "echo-base")

    recs = sign(z3, 3)
    rec = recs[0]

    assert hub.on_record("echo-base/z3-r0/records", encode_record(rec)) is True

    # wrong swarm topic
    assert hub.on_record("rogue-squadron/z3-r0/records", encode_record(rec)) is False
    # mismatched agent: payload says "stranger", topic says z3-r0
    bad_agent = dict(rec)
    bad_agent["agent"] = "stranger"
    assert hub.on_record("echo-base/z3-r0/records", encode_record(bad_agent)) is False
    # non-JSON payload
    assert hub.on_record("echo-base/z3-r0/records", b"not json") is False

    assert hub.dropped == 3


def test_on_record_from_a_thread(mesh, home, outbox):
    hub_client, z3 = _setup(mesh, home)
    publish = lambda t, p, retain=False: None
    hub = Hub(hub_client, outbox, publish, "echo-base")

    recs = sign(z3, 3)
    results = []

    def worker(rec):
        results.append(hub.on_record("echo-base/z3-r0/records", encode_record(rec)))

    threads = [threading.Thread(target=worker, args=(r,)) for r in recs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results)
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [1, 2, 3]


def test_tick_relays_and_publishes_ack(mesh, home, outbox):
    hub_client, z3 = _setup(mesh, home)
    sent = []
    publish = lambda t, p, retain=False: sent.append((t, p, retain))
    hub = Hub(hub_client, outbox, publish, "echo-base")

    recs = sign(z3, 3)
    for r in recs:
        assert hub.on_record("echo-base/z3-r0/records", encode_record(r))

    result = hub.tick()

    assert result["heads"]["z3-r0"]["last_ack_seq"] == 3
    assert result["errors"] == {}
    assert outbox.pending("z3-r0") == []

    assert len(sent) == 1
    topic, payload, retain = sent[0]
    assert topic == "echo-base/z3-r0/acks"
    assert retain is True
    assert decode_ack(payload)["last_ack_seq"] == 3


def test_tick_tampered_record_stuck(mesh, home, outbox):
    hub_client, z3 = _setup(mesh, home)
    publish = lambda t, p, retain=False: None
    hub = Hub(hub_client, outbox, publish, "echo-base")

    recs = sign(z3, 3)
    recs[1]["summary"] = "changed"  # tampered after signing, not re-signed
    for r in recs:
        hub.on_record("echo-base/z3-r0/records", encode_record(r))

    result = hub.tick()

    assert result["heads"]["z3-r0"]["last_ack_seq"] == 1
    assert hub.stuck["z3-r0"]["code"] == "hash"
    # seq 1 was accepted and pruned; the tampered seq 2 and seq 3 stay queued
    assert [r["seq"] for r in outbox.pending("z3-r0")] == [2, 3]


def test_tick_client_down_publishes_nothing(mesh, home, outbox):
    class DownClient:
        def relay(self, records):
            raise httpx.ConnectError("down")

    hub_client, z3 = _setup(mesh, home)
    sent = []
    publish = lambda t, p, retain=False: sent.append((t, p, retain))
    hub = Hub(DownClient(), outbox, publish, "echo-base")

    recs = sign(z3, 1)
    for r in recs:
        hub.on_record("echo-base/z3-r0/records", encode_record(r))

    before = [r["seq"] for r in outbox.pending("z3-r0")]
    assert hub.tick() is None
    assert sent == []
    assert [r["seq"] for r in outbox.pending("z3-r0")] == before
