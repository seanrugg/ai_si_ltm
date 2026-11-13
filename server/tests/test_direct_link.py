"""Direct mode (#297): a member's own outbox over HTTPS, and the auto switch."""
from __future__ import annotations

import httpx
import pytest

from cli_agent.direct_link import DirectLink, Transport
from cli_agent.member_link import MemberLink
from cli_agent.member_main import load_config
from cli_agent.outbox import Outbox
from cli_agent.agent import fetch_all, verify_chain


def _link(client, tmp_path):
    outbox = Outbox(tmp_path / "out.db")
    link = MemberLink(client.name, client.key, outbox, tmp_path / "head.json", lambda *a, **k: None, "unused")
    return link, outbox


def test_direct_mode_sends_the_outbox_and_the_chain_verifies(mesh, tmp_path):
    z3 = mesh.member("z3-r0")
    link, outbox = _link(z3, tmp_path)
    for i in range(3):
        link.append("perception", summary="heartbeat", tags=["telemetry", "internal"], payload={"i": i})
    direct = DirectLink(z3, outbox, "z3-r0")
    assert direct.flush() == 3
    assert outbox.pending("z3-r0") == []
    recs = z3.records(agent="z3-r0")
    assert [r["seq"] for r in sorted(recs, key=lambda r: r["seq"])] == [1, 2, 3]
    # Two more, and a re-send of an already-acked record is a harmless duplicate.
    link.append("perception", summary="heartbeat", payload={"i": 3})
    assert direct.flush() == 1
    assert z3.sync_head()["last_ack_seq"] == 4
    ok, msg = verify_chain(fetch_all(z3), bytes(z3.key.verify_key))
    assert ok, msg


def test_direct_flush_never_raises_when_ltms_is_down(mesh, tmp_path):
    z3 = mesh.member("z3-r0")
    link, outbox = _link(z3, tmp_path)
    link.append("event", summary="x")

    class Down:
        def _call(self, *a, **k):
            raise httpx.ConnectError("down")
    assert DirectLink(Down(), outbox, "z3-r0").flush() == 0
    assert len(outbox.pending("z3-r0")) == 1


def test_auto_transport_falls_back_and_returns():
    t = Transport("auto", fallback_after=120, now=0.0)
    assert t.choose(10) == "mqtt"          # never connected yet, but not long enough
    t.on_connect()
    assert t.choose(50) == "mqtt"
    t.on_disconnect(100.0)
    assert t.choose(219.9) == "mqtt"       # down 119.9 s
    assert t.choose(220.0) == "direct"     # down 120 s
    t.on_connect()
    assert t.choose(221.0) == "mqtt"       # back as soon as it reconnects


def test_auto_without_ltms_stays_on_mqtt():
    t = Transport("auto", fallback_after=1, now=0.0, can_direct=False)
    assert t.choose(1000) == "mqtt"


def test_fixed_modes():
    assert Transport("direct", 1, 0.0).choose(5) == "direct"
    assert Transport("mqtt", 1, 0.0).choose(5000) == "mqtt"


def test_config_transport():
    base = {"LTMS_KEY_PATH": "k.json", "SWARM": "echo-base"}
    assert load_config(base).transport == "auto" and load_config(base).fallback_after_seconds == 120.0
    assert load_config({"LTMS_KEY_PATH": "k.json", "TRANSPORT": "direct", "LTMS_SERVER": "http://x"}).transport == "direct"
    with pytest.raises(ValueError, match="LTMS_SERVER"):
        load_config({**base, "TRANSPORT": "direct"})
    with pytest.raises(ValueError, match="TRANSPORT"):
        load_config({**base, "TRANSPORT": "pigeon"})
    with pytest.raises(ValueError, match="FALLBACK_AFTER_SECONDS"):
        load_config({**base, "FALLBACK_AFTER_SECONDS": "0"})
