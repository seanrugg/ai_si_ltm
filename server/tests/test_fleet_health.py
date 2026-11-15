"""Fleet health page (#381): last heard, heartbeat status, hub health, specs and ping."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.services import fleet_health
from server.tests.test_swarms import Home

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec, name="alpha")


def _beats(client, interval_s: int, n: int = 4):
    for i in range(n):
        client.append("perception", summary="heartbeat", tags=["telemetry", "internal"],
                      payload={"uptime_s": i * interval_s}, ts=_ts(T0 + timedelta(seconds=i * interval_s)))
    client.flush()


def _rows(db_exec, now=None):
    out = db_exec(lambda s: fleet_health.fleet(s, now=now))
    return {r["name"]: r for r in out["members"]}


def test_interval_and_thresholds_unit():
    times = [T0 + timedelta(seconds=s) for s in (0, 60, 120, 180, 600)]
    assert fleet_health.interval_of(times) == 60.0   # median gap, one long gap ignored
    assert fleet_health.interval_of(times[:1]) is None
    heard = T0
    assert fleet_health.status_of(heard, 60, T0 + timedelta(seconds=90)) == "on_time"   # 1.5x exactly
    assert fleet_health.status_of(heard, 60, T0 + timedelta(seconds=91)) == "late"
    assert fleet_health.status_of(heard, 60, T0 + timedelta(seconds=120)) == "late"     # 2x exactly
    assert fleet_health.status_of(heard, 60, T0 + timedelta(seconds=121)) == "silent"
    assert fleet_health.status_of(heard, None, T0) == "unknown"
    assert fleet_health.status_of(None, 60, T0) == "never"


def test_status_follows_each_members_own_interval(mesh, db_exec):
    fast, slow = mesh.member("fast"), mesh.member("slow")
    _beats(fast, 60)
    _beats(slow, 600)
    rows = _rows(db_exec)
    assert rows["fast"]["heartbeat"]["interval_s"] == 60.0
    assert rows["slow"]["heartbeat"]["interval_s"] == 600.0
    assert rows["fast"]["heartbeat"]["self_report"] is True
    heard = datetime.fromisoformat(rows["fast"]["last_heard"])
    # 100 s after last heard: late for a 60 s member, on time for a 600 s one.
    rows = _rows(db_exec, now=heard + timedelta(seconds=100))
    assert rows["fast"]["status"] == "late"
    assert rows["slow"]["status"] == "on_time"
    rows = _rows(db_exec, now=heard + timedelta(seconds=130))
    assert rows["fast"]["status"] == "silent"
    rows = _rows(db_exec, now=heard + timedelta(seconds=30))
    assert rows["fast"]["status"] == "on_time"


def test_last_heard_counts_records_and_unknown_without_heartbeats(mesh, db_exec):
    quiet, talker = mesh.member("quiet"), mesh.member("talker", kind="assistant")
    talker.append("thought", summary="hello")
    talker.flush()
    rows = _rows(db_exec)
    assert rows["quiet"]["last_heard"] is None and rows["quiet"]["status"] == "never"
    assert rows["talker"]["last_heard"] == rows["talker"]["last_record"]
    assert rows["talker"]["status"] == "unknown"
    assert rows["talker"]["kind"] == "chat member"
    assert rows["quiet"]["kind"] == "robot"


def test_health_only_when_reported_and_grouping(mesh, admin, home, db_exec):
    hub, bot, free = mesh.member("hub-a", kind="relay"), mesh.member("bot-a"), mesh.member("loner", kind="agent")
    home.add(bot)
    home.appoint(hub)
    hub.append("perception", summary="machine health", tags=["telemetry", "internal", "health"],
               payload={"sensor": "jetson-health", "temp_c": {"gpu": {"min": 60, "max": 72, "last": 71}},
                        "services": {"wire-pod": "active"}, "throttled": True})
    hub.append("event", summary="throttle: gpu 86 C", tags=["internal", "health", "throttle"],
               payload={"sensor": "jetson-health"})
    hub.flush()
    _beats(bot, 60)
    r = admin.get("/api/admin/health")
    assert r.status_code == 200, r.text
    rows = {m["name"]: m for m in r.json()["members"]}
    h = rows["hub-a"]["health"]
    assert h["self_report"] is True and h["throttle_events_24h"] == 1
    assert h["payload"]["temp_c"]["gpu"]["last"] == 71
    assert rows["bot-a"]["health"] is None and rows["loner"]["health"] is None
    # health self-reports aren't heartbeats
    assert rows["hub-a"]["heartbeat"]["samples"] == 0
    assert rows["hub-a"]["kind"] == "hub" and rows["hub-a"]["swarm"] == "alpha"
    assert rows["bot-a"]["swarm"] == "alpha" and rows["loner"]["swarm"] is None
    assert rows["loner"]["kind"] == "member"


def test_specs_and_ping_in_rows(mesh, admin, db_exec):
    m = mesh.member("spec-bot")
    r = admin.patch(f"/api/admin/members/{m.member_id}/specs",
                    json={"hardware": {"board": "Dev Board", "ram_mib": 8192},
                          "models": [{"name": "small-model", "runtime": "Ollama"}]})
    assert r.status_code == 200, r.text
    assert admin.post("/api/admin/members/spec-bot/ping").status_code == 200
    m.append("perception", summary="heartbeat (ping)", tags=["telemetry", "internal"], payload={"ping": True})
    m.flush()
    row = _rows(db_exec)["spec-bot"]
    assert row["specs"]["hardware"]["board"] == "Dev Board"
    assert row["specs"]["models"][0]["name"] == "small-model"
    assert row["spec_summary"] == "Dev Board 8 GB · small-model"
    assert row["ping"]["answered_at"] and row["last_ping_reply"] == row["ping"]["answered_at"]
    assert row["last_heard"] is not None


def test_only_active_members_and_admin_only(mesh, http, admin):
    mesh.member("pending-one", approve=False)
    names = [m["name"] for m in admin.get("/api/admin/health").json()["members"]]
    assert "pending-one" not in names
    http.cookies.clear()
    assert http.get("/api/admin/health").status_code == 401
