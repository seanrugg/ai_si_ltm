"""Admin ping (issue #356): ask a member for a heartbeat; its hub or itself picks it up."""
from __future__ import annotations

import types

import pytest

from cli_agent.hub_main import deliver_pings
from cli_agent.member_main import heartbeat, load_config, make_on_message
from server.tests.test_datasets_read import _get
from server.tests.test_swarms import Home


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _pings(member):
    r = _get(member, "/api/members/me/pings")
    assert r.status_code == 200, r.text
    return r.json()["pings"]


def test_ping_lifecycle_through_the_hub(mesh, admin, home):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    home.add(z3)
    home.appoint(hub)
    assert admin.get("/api/admin/members/z3-r0/ping").json()["requested_at"] is None

    r = admin.post("/api/admin/members/z3-r0/ping")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["requested_at"] and s["requested_by"].startswith("admin:")
    assert s["delivered_at"] is None and s["answered_at"] is None

    assert _pings(hub) == ["z3-r0"]          # the hub picks it up for its member
    assert _pings(hub) == []                 # once
    assert admin.get("/api/admin/members/z3-r0/ping").json()["delivered_at"]

    z3.append("perception", summary="heartbeat (ping)", tags=["telemetry", "internal"], payload={"ping": True})
    z3.flush()
    s = admin.get("/api/admin/members/z3-r0/ping").json()
    assert s["answered_at"] and s["answer"]["summary"] == "heartbeat (ping)"


def test_own_ping_and_other_swarms(mesh, admin, home, db_exec):
    hub, z3 = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0")
    other = mesh.member("stranger")
    home.add(z3)
    home.appoint(hub)
    admin.post("/api/admin/members/stranger/ping")
    admin.post("/api/admin/members/jetson-hub/ping")
    assert _pings(hub) == ["jetson-hub"]     # its own, never a member of another swarm
    assert _pings(other) == ["stranger"]     # a direct member fetches its own
    assert _pings(z3) == []
    assert admin.post("/api/admin/members/nobody/ping").status_code == 404


def test_hub_answers_its_own_and_passes_members_on():
    published, signed = [], []
    hub = types.SimpleNamespace(name="jetson-hub", prefixes=["sw-1234abcd"],
                                publish=lambda t, p, retain=False: published.append((t, p, retain)),
                                heartbeat=lambda link, s, n, ping=False: signed.append(ping))
    client = types.SimpleNamespace(pings=lambda: ["jetson-hub", "z3-r0"])
    assert deliver_pings(hub, None, client, 0, 1) == ["jetson-hub", "z3-r0"]
    assert signed == [True] and published == [("sw-1234abcd/z3-r0/ping", b"", False)]


def test_member_answers_a_ping_message_and_heartbeats_are_twice_daily():
    import queue
    import threading

    pinged = threading.Event()
    on_message = make_on_message(queue.Queue(), queue.Queue(), pinged)
    on_message(None, None, types.SimpleNamespace(topic="sw-1234abcd/z3-r0/ping", payload=b""))
    assert pinged.is_set()
    signed = []
    link = types.SimpleNamespace(append=lambda t, **k: signed.append((t, k)) or k)
    heartbeat(link, 0, 5, ping=True)
    assert signed[0][1]["payload"] == {"uptime_s": 5, "ping": True}
    cfg = load_config({"LTMS_KEY_PATH": "k", "SWARM_TOPIC_ID": "sw-1234abcd"})
    assert cfg.heartbeat_seconds == 43200 and cfg.resend_seconds == 60 and cfg.ping_poll_seconds == 30
