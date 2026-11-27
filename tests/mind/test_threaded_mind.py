"""A mind on its own thread, signing through its host's loop (#519)."""
from __future__ import annotations

import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout

import pytest

from cli_agent.member_link import MemberLink
from cli_agent.outbox import Outbox
from ltms_contract import GENESIS_HASH
from ltms_mind.brains.none import NoBrain
from ltms_mind.embed import HostQueue, ThreadedMind, embedded_config

from .conftest import FakeClient, ScriptedBrain

ENV = {"LTMS_SERVER": "https://ltms.example.org", "LTMS_KEY_PATH": "/k/v3-c7r.key.json"}


def host(tmp_path, server):
    client = FakeClient(server, name="v3-c7r")
    link = MemberLink("v3-c7r", client.key, Outbox(tmp_path / "o.db"), tmp_path / "head.json",
                      lambda *a, **k: None, "sw-1")
    return client, link


def run_with_pump(mind, link, now):
    """The mind wakes on its thread while the 'host loop' pumps on this one."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("counts", mind.tick(now)))
    t.start()
    while t.is_alive():
        mind.pump(link)
        time.sleep(0.005)
    mind.pump(link)
    return out["counts"]


def chain_ok(link):
    prev = GENESIS_HASH
    recs = link._outbox.pending("v3-c7r", limit=1000)
    for r in recs:
        assert r["prev_hash"] == prev
        prev = r["hash"]
    return recs


def test_records_are_signed_only_by_the_host_loop(tmp_path, server):
    q = HostQueue(timeout=5)
    _, link = host(tmp_path, server)
    got = {}
    t = threading.Thread(target=lambda: got.setdefault("rec", q.sign("event", summary="hello")))
    t.start()
    time.sleep(0.05)
    assert link.head["seq"] == 0                 # nothing signed until the host pumps
    while t.is_alive():
        q.pump(link)
        time.sleep(0.005)
    assert got["rec"]["seq"] == 1 and q.head["seq"] == 1


def test_sign_times_out_when_the_host_stops_pumping():
    with pytest.raises(FutureTimeout):
        HostQueue(timeout=0.05).sign("event", summary="nobody home")


def test_dreams_with_its_brain_on_the_charger_and_does_duties_only_off_it(tmp_path, server):
    client, link = host(tmp_path, server)
    cfg = embedded_config(ENV, str(tmp_path), force={"MIND_EMBEDDER": "lexical", "MIND_HEALTH": "off"})
    docked = {"on": False}
    thinker = ScriptedBrain()
    mind = ThreadedMind(cfg, 3600, client, brain=thinker, awake_brain=NoBrain(),
                        dream_when=lambda: docked["on"], clock=lambda: 0.0)

    counts = run_with_pump(mind, link, 0.0)            # off the dock: awake, no model
    assert counts["mode"] == "awake" and counts["brain"] == "none"

    docked["on"] = True
    counts = run_with_pump(mind, link, 3600.0)         # docked: dreams with its brain
    assert counts["mode"] == "dreaming" and counts["brain"] == "scripted"
    recs = chain_ok(link)                              # one chain, signed by the host
    assert [r["summary"] for r in recs if "mind_cycle" in (r.get("tags") or [])][-1] == "Dream cycle ended"
    assert client.outbox == []
    assert mind.mind.cfg.dream.mode == cfg.dream.mode  # the per-wake override doesn't stick


def test_without_dream_when_mind_dream_decides(tmp_path, server):
    client, link = host(tmp_path, server)
    cfg = embedded_config({**ENV, "MIND_DREAM": "never"}, str(tmp_path), force={"MIND_EMBEDDER": "lexical"})
    mind = ThreadedMind(cfg, 60, client, brain=ScriptedBrain(), clock=lambda: 0.0)
    assert run_with_pump(mind, link, 0.0)["mode"] == "awake"


def test_start_and_stop(tmp_path, server):
    client, link = host(tmp_path, server)
    cfg = embedded_config(ENV, str(tmp_path), force={"MIND_EMBEDDER": "lexical", "MIND_HEALTH": "off"})
    mind = ThreadedMind(cfg, 3600, client, brain=NoBrain(), poll_seconds=0.01)
    mind.start()
    deadline = time.time() + 5
    while time.time() < deadline and not [r for r in link._outbox.pending("v3-c7r", 100)
                                          if "mind_cycle" in (r.get("tags") or [])]:
        mind.pump(link)
        time.sleep(0.01)
    mind.stop()
    assert any("mind_cycle" in (r.get("tags") or []) for r in chain_ok(link))
