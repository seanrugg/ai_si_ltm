"""The hub as a member (#344): the no-brain mind inside the hub process, signing into
the hub's own chain, and relay health on change."""
from __future__ import annotations

import threading

from cli_agent.hub_main import (
    HubContext,
    load_extensions,
    make_on_connect,
    make_on_disconnect,
)
from cli_agent.hub_relay import RelayWatch
from cli_agent.member_link import MemberLink
from cli_agent.outbox import Outbox
from ltms_contract import GENESIS_HASH
from ltms_mind.hub import HubMind, LinkedClient, mind_config

from .conftest import FakeClient
from .test_mind_directives import FLEET, SWARM, directive

HUB = "jetson-hub"
ENV = {"LTMS_SERVER": "https://ltms.example.org", "LTMS_KEY_PATH": "/etc/hub/jetson-hub.key.json"}


def hub_parts(tmp_path, server):
    client = FakeClient(server, name=HUB)
    outbox = Outbox(tmp_path / "hub-outbox.db")
    link = MemberLink(HUB, client.key, outbox, tmp_path / "hub-head.json", lambda *a, **k: None, "sw-1")
    cfg, every = mind_config({**ENV, "MIND_STATE_PATH": str(tmp_path / "hub-mind.json")},
                             str(tmp_path / "hub-head.json"))
    return client, outbox, link, HubMind(cfg, every, client, link, threading.Lock(), now=0.0)


def chain(outbox):
    return outbox.pending(HUB, limit=1000)


# -- the mind on the hub's own chain ---------------------------------------------------

def test_the_mind_signs_into_the_hubs_chain_with_no_fork(tmp_path, server):
    server.swarm = SWARM
    client, outbox, link, mind = hub_parts(tmp_path, server)
    link.append("perception", summary="heartbeat", tags=["telemetry", "internal"], payload={"role": "hub"})
    assert mind.due(0.0)
    counts = mind.wake(0.0)
    link.append("perception", summary="heartbeat", tags=["telemetry", "internal"], payload={"role": "hub"})

    recs = chain(outbox)
    assert [r["seq"] for r in recs] == list(range(1, len(recs) + 1))
    prev = GENESIS_HASH
    for r in recs:                                     # one continuous chain: no fork
        assert r["prev_hash"] == prev
        prev = r["hash"]
    tags = [t for r in recs for t in (r.get("tags") or [])]
    assert "introduction" in tags and "mind_cycle" in tags
    assert counts["brain"] == "none" and counts["duties"]["introduced"] == 1
    assert server.charter_reads == 1 and client.outbox == []   # nothing queued outside the hub's outbox
    assert not [r for r in server.records if r["agent"] == HUB]  # the hub's relay delivers, not the mind


def test_the_mind_wakes_on_its_interval_only(tmp_path, server):
    *_, mind = hub_parts(tmp_path, server)
    mind.wake(0.0)
    assert not mind.due(899.0) and mind.due(900.0)


def test_a_directive_is_acknowledged_and_followed(tmp_path, server):
    server.swarm = SWARM
    server.fleet_public = FLEET.verify_key.encode().hex()
    server.directives = [directive("Report relay health")]
    _, outbox, _, mind = hub_parts(tmp_path, server)
    mind.wake(0.0)
    acks = [r["payload"]["status"] for r in chain(outbox) if "directive-ack" in (r.get("tags") or [])]
    assert acks == ["received", "accepted"]           # no brain: no reason to decline


def test_the_mind_reports_no_machine_health_hub_health_does(tmp_path, server):
    _, outbox, _, mind = hub_parts(tmp_path, server)
    mind.wake(0.0)
    assert not [r for r in chain(outbox) if r["type"] == "perception" and "health" in (r.get("tags") or [])]


def test_a_failing_wake_never_raises(tmp_path, server):
    server.online = False
    *_, mind = hub_parts(tmp_path, server)
    mind.wake(0.0)                                     # offline: logged, relay goes on
    assert mind.due(900.0)


# -- settings ---------------------------------------------------------------------------

def test_mind_config_defaults_and_off(tmp_path):
    cfg, every = mind_config(ENV, str(tmp_path / "hub-head.json"))
    assert every == 900.0 and cfg.brain == "none" and cfg.health is False and cfg.embedder == "lexical"
    assert cfg.state_path == str(tmp_path / "hub-mind.json")
    assert mind_config({**ENV, "MIND_BRAIN": "claude-code"}, str(tmp_path / "h.json"))[0].brain == "none"
    assert mind_config({**ENV, "HUB_MIND_SECONDS": "0"}, str(tmp_path / "h.json")) is None


def test_linked_client_reads_from_the_client_and_signs_through_the_link(tmp_path, server):
    client = FakeClient(server, name=HUB)
    signed = []
    lc = LinkedClient(client, lambda type, **kw: signed.append((type, kw)) or {"seq": 1},
                      lambda: {"seq": 7, "hash": "h7"})
    lc.auto_read_charter = False
    assert client.auto_read_charter is False          # the client itself must see it
    assert (lc.seq, lc.last_hash, lc.name) == (7, "h7", HUB)
    lc.outbox = [{"seq": 3}]
    assert lc.outbox == [] and lc.flush() == []
    lc.append("event", summary="x")
    assert signed == [("event", {"summary": "x"})] and client.outbox == []


# -- relay health on change -------------------------------------------------------------

class Signer:
    def __init__(self):
        self.recs = []

    def __call__(self, type, **kw):
        self.recs.append({"type": type, **kw})
        return {"agent": HUB, "seq": len(self.recs)}


def test_backlog_over_the_threshold_writes_one_event_then_one_on_recovery():
    w, sign = RelayWatch(backlog_max=500, hold_s=60), Signer()
    for t in range(0, 300, 10):
        w.record(sign, w.observe(float(t), backlog=800))
    assert len(sign.recs) == 1                         # held 60 s: one event, then nothing more
    first = sign.recs[0]
    assert first["summary"] == "relay backlog: high" and first["tags"] == ["internal", "health", "relay", "backlog"]
    assert first["payload"]["records"] == 800 and first["payload"]["previous"] == "ok" and "refs" not in first
    for t in range(300, 400, 10):
        w.record(sign, w.observe(float(t), backlog=20))
    assert len(sign.recs) == 2
    assert sign.recs[1]["payload"]["state"] == "ok" and sign.recs[1]["refs"] == {"previous": f"{HUB}:1"}


def test_a_flap_shorter_than_the_hold_writes_nothing():
    w, sign = RelayWatch(hold_s=60), Signer()
    w.record(sign, w.observe(0.0, bus_up=False))
    w.record(sign, w.observe(30.0, bus_up=False))
    w.record(sign, w.observe(40.0, bus_up=True))       # back before 60 s
    w.record(sign, w.observe(200.0, bus_up=True))
    assert sign.recs == []


def test_bus_and_relay_changes_and_a_healthy_start_writes_nothing():
    w, sign = RelayWatch(hold_s=0), Signer()
    w.record(sign, w.observe(0.0, backlog=0, bus_up=True, relay_ok=True))
    assert sign.recs == []
    w.record(sign, w.observe(1.0, bus_up=None, relay_ok=False))   # bus not known yet: skipped
    w.record(sign, w.observe(2.0, bus_up=False))
    assert [r["summary"] for r in sign.recs] == ["relay relay: failing", "relay bus: down"]


def test_relay_watch_from_env():
    w = RelayWatch.from_env({"HUB_BACKLOG_MAX": "50", "HUB_RELAY_HOLD_SECONDS": "5"})
    assert (w.backlog_max, w.hold_s) == (50, 5.0)


def test_outbox_count(tmp_path, server):
    _, outbox, link, _ = hub_parts(tmp_path, server)
    assert outbox.count() == 0
    link.append("event", summary="a")
    link.append("event", summary="b")
    assert outbox.count() == 2


def test_mqtt_callbacks_track_the_bus():
    bus = {"up": None}
    subs = []
    client = type("C", (), {"subscribe": lambda self, t, qos=1: subs.append(t)})()
    make_on_connect(["sw-1"], bus)(client, None, None, 0, None)
    assert bus["up"] is True and subs
    make_on_disconnect(bus)(client, None, None, 7, None)
    assert bus["up"] is False


# -- the hub's extension hook --------------------------------------------------------------

def test_the_hub_loads_the_mind_as_an_extension(tmp_path, server):
    client = FakeClient(server, name=HUB)
    link = MemberLink(HUB, client.key, Outbox(tmp_path / "o.db"), tmp_path / "hub-head.json",
                      lambda *a, **k: None, "sw-1")
    ctx = HubContext(env={**ENV, "MIND_STATE_PATH": str(tmp_path / "m.json")}, client=client, link=link,
                     lock=threading.Lock(), state_path=str(tmp_path / "hub-head.json"), started_at=0.0)
    (mind,) = load_extensions("ltms_mind.hub:extension", ctx)
    assert isinstance(mind, HubMind)
    mind.tick(0.0)
    assert link.head["seq"] >= 1                      # the cycle event is on the hub's chain
    off = HubContext(**{**ctx.__dict__, "env": {**ctx.env, "HUB_MIND_SECONDS": "0"}})
    assert load_extensions("ltms_mind.hub:extension", off) == []


def test_a_broken_extension_is_skipped():
    ctx = HubContext(env={}, client=None, link=None, lock=None, state_path="x", started_at=0.0)
    assert load_extensions("no_such_module:thing, ,", ctx) == []
