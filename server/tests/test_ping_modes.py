"""How a member can answer a ping (issue #384): declared, admin-set or inferred
answer mode; ping turned off for ``none``; "passed to the hub" vs "picked up by
the member"; hubs handed pings only for members that may be on the bus; on-use
members idle rather than silent."""
from __future__ import annotations

import threading
import types
from datetime import datetime, timedelta, timezone

import pytest

from cli_agent.hub_main import deliver_pings
from server.services import attention
from server.tests.test_attention import _by_key, _service
from server.tests.test_fleet_health import _beats, _rows
from server.tests.test_ping import _pings
from server.tests.test_specs import _put
from server.tests.test_swarms import Home


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _status(admin, name):
    r = admin.get(f"/api/admin/members/{name}/ping")
    assert r.status_code == 200, r.text
    return r.json()


def test_mode_is_declared_then_admin_overrides_it(mesh, admin):
    bot = mesh.member("bot")
    assert _status(admin, "bot")["mode"] == {"mode": "unknown", "source": None, "reason": None}
    assert _put(bot, {"software": {"ping": "polling"}}).status_code == 200
    assert _status(admin, "bot")["mode"] == {"mode": "polling", "source": "declared", "reason": None}
    r = admin.patch(f"/api/admin/members/{bot.member_id}/specs",
                    json={"software": {"ping": "on_use"}})
    assert r.status_code == 200, r.text
    assert _status(admin, "bot")["mode"]["mode"] == "on_use"
    assert _status(admin, "bot")["mode"]["source"] == "admin"


def test_bad_mode_is_refused(mesh):
    bot = mesh.member("bot")
    r = _put(bot, {"software": {"ping": "sometimes"}})
    assert r.status_code == 422 and "polling, on_use, none" in r.text
    assert _put(bot, {"software": {"ping": "none", "ping_reason": "x" * 201}}).status_code == 422


def test_mode_is_inferred_from_heartbeats_or_kind(mesh, admin):
    beating, chat, quiet = mesh.member("beating"), mesh.member("chat", kind="assistant"), mesh.member("quiet")
    _beats(beating, 600)
    assert _status(admin, "beating")["mode"] == {"mode": "polling", "source": "inferred", "reason": None}
    assert _status(admin, "chat")["mode"] == {"mode": "on_use", "source": "inferred", "reason": None}
    assert _status(admin, "quiet")["mode"]["mode"] == "unknown"


def test_none_turns_ping_off_with_the_reason(mesh, admin):
    held = mesh.member("held")
    _put(held, {"software": {"ping": "none", "ping_reason": "an admin submits for this member"}})
    s = _status(admin, "held")
    assert s["mode"] == {"mode": "none", "source": "declared", "reason": "an admin submits for this member"}
    r = admin.post("/api/admin/members/held/ping")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ping_off"
    assert "an admin submits for this member" in r.json()["error"]["message"]
    assert _status(admin, "held")["requested_at"] is None


def test_delivered_via_hub_or_member(mesh, admin, home):
    hub, z3, solo = mesh.member("jetson-hub", kind="relay"), mesh.member("z3-r0"), mesh.member("solo")
    home.add(z3)
    home.appoint(hub)
    admin.post("/api/admin/members/z3-r0/ping")
    admin.post("/api/admin/members/solo/ping")
    assert _pings(hub) == ["z3-r0"]
    assert _pings(solo) == ["solo"]
    s = _status(admin, "z3-r0")
    assert s["delivered_by"] == "jetson-hub" and s["delivered_via"] == "hub"
    s = _status(admin, "solo")
    assert s["delivered_by"] == "solo" and s["delivered_via"] == "member"
    admin.post("/api/admin/members/z3-r0/ping")  # a new ping clears the old delivery
    s = _status(admin, "z3-r0")
    assert s["delivered_at"] is None and s["delivered_by"] is None and s["delivered_via"] is None


def test_hub_is_not_handed_pings_for_on_use_members(mesh, admin, home):
    hub = mesh.member("jetson-hub", kind="relay")
    chat, z3 = mesh.member("test-claude-probe", kind="assistant"), mesh.member("z3-r0")
    home.add(chat)
    home.add(z3)
    home.appoint(hub)
    admin.post("/api/admin/members/test-claude-probe/ping")
    admin.post("/api/admin/members/z3-r0/ping")
    assert _pings(hub) == ["z3-r0"]                 # not the chat member: it isn't on MQTT
    assert _status(admin, "test-claude-probe")["delivered_at"] is None
    assert _pings(chat) == ["test-claude-probe"]    # it fetches its own when next used
    assert _status(admin, "test-claude-probe")["delivered_via"] == "member"


def test_on_use_member_is_idle_not_silent(app, mesh, db_exec):
    chat = mesh.member("chat", kind="assistant")
    chat.append("action", summary="answered a question", outcome="success")
    chat.flush()
    later = datetime.now(timezone.utc) + timedelta(hours=49)
    assert _rows(db_exec, now=later)["chat"]["status"] == "idle"
    assert _rows(db_exec, now=later)["chat"]["ping"]["mode"]["mode"] == "on_use"
    assert "silent:chat" not in _by_key(_service(app, db_exec, now=later))


def test_idle_only_replaces_quiet_statuses():
    from server.services.fleet_health import idle_or

    assert idle_or("silent", "on_use") == "idle"
    assert idle_or("unknown", "on_use") == "idle"
    assert idle_or("on_time", "on_use") == "on_time"
    assert idle_or("never", "on_use") == "never"
    assert idle_or("silent", "polling") == "silent"
    assert attention.is_silent({"last_heard": "2020-01-01T00:00:00+00:00", "status": "idle"},
                               datetime.now(timezone.utc)) is False


def test_hub_passes_on_only_to_members_heard_on_the_bus():
    published, signed = [], []
    hub = types.SimpleNamespace(name="jetson-hub", prefixes=["sw-1234abcd"], heard={"z3-r0"}, lock=threading.Lock(),
                                publish=lambda t, p, retain=False: published.append((t, p, retain)),
                                heartbeat=lambda link, s, n, ping=False: signed.append(ping))
    client = types.SimpleNamespace(pings=lambda: ["jetson-hub", "z3-r0", "test-claude-probe"])
    assert deliver_pings(hub, None, client, 0, 1) == ["jetson-hub", "z3-r0"]
    assert signed == [True] and published == [("sw-1234abcd/z3-r0/ping", b"", False)]


def test_hub_records_who_it_heard():
    from cli_agent.hub import Hub

    hub = Hub(client=None, outbox=types.SimpleNamespace(add=lambda rec: True), publish=lambda *a, **k: None,
              swarm="sw-1234abcd")
    hub.on_hello("sw-1234abcd/z3-r0/hello", b'{"agent": "z3-r0"}')
    assert "z3-r0" in hub.heard
