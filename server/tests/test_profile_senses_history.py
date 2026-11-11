"""Profile Senses and History (issue #188)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from server.models import Record, RecordPayload
from server.tests.test_swarms import Home


@pytest.fixture
def home(mesh, db_exec):
    return Home(mesh, db_exec)


def _get(admin, path, **params):
    r = admin.get(f"/api/admin/profile/{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ts(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _tof(z3, minutes_ago, values):
    return z3.append("perception", summary="tof window", tags=["telemetry"], ts=_ts(minutes_ago), payload={
        "kind": "sensor", "sensor": "front-tof", "modality": "range", "unit": "mm", "rate_hz": 10,
        "samples": [{"t": i * 0.1, "v": v} for i, v in enumerate(values)]})


def test_two_numeric_sensors(mesh, admin):
    z3 = mesh.member("z3-r0")
    for i in range(60):  # one window every 20 minutes, 20 hours
        _tof(z3, 1200 - i * 20, [100 + i, 110 + i])
    newest = _tof(z3, 1, [131, 118])
    z3.append("perception", summary="battery", tags=["telemetry"], ts=_ts(5),
              payload={"kind": "battery", "unit": "%", "samples": [{"t": 0, "v": 87}]})
    z3.append("perception", summary="obstacle ahead", ts=_ts(0),
              refs={"derived_from": [f"z3-r0:{newest['seq']}"]})
    z3.flush()
    sensors = {s["sensor"]: s for s in _get(admin, "senses", member="z3-r0")["sensors"]}
    assert set(sensors) == {"front-tof", "battery"}
    tof = sensors["front-tof"]
    assert (tof["member"], tof["modality"], tof["unit"], tof["rate_hz"]) == ("z3-r0", "range", "mm", 10)
    assert tof["records"] == 61 and tof["feeds"] == 1
    assert tof["last"]["value"] == 118  # the newest sample (t = 0.1)
    assert 1 < len(tof["series"]) <= 48
    assert all(isinstance(p["v"], float) for p in tof["series"])
    bat = sensors["battery"]
    assert bat["modality"] is None and bat["unit"] == "%" and bat["last"]["value"] == 87 and bat["feeds"] == 0


def test_non_numeric_samples_have_no_series(mesh, admin):
    z3 = mesh.member("z3-r0")
    z3.append("perception", summary="imu", tags=["telemetry"], payload={
        "kind": "sensor", "sensor": "imu", "samples": [{"t": 0, "v": [0.1, -0.4, 9.8]}]})
    z3.flush()
    (imu,) = _get(admin, "senses", member="z3-r0")["sensors"]
    assert imu["last"]["value"] == [0.1, -0.4, 9.8]
    assert imu["series"] == []


def test_a_purged_payload_is_counted_but_ignored(mesh, admin, db_exec):
    z3 = mesh.member("z3-r0")
    _tof(z3, 10, [120])
    gone = _tof(z3, 5, [999])
    z3.flush()

    async def purge(session):
        rid = await session.scalar(select(Record.id).where(Record.agent == "z3-r0", Record.seq == gone["seq"]))
        await session.execute(delete(RecordPayload).where(RecordPayload.record_id == rid))
    db_exec(purge)
    sensors = _get(admin, "senses", member="z3-r0")["sensors"]
    tof = next(s for s in sensors if s["sensor"] == "front-tof")
    assert tof["records"] == 1 and tof["last"]["value"] == 120
    purged = next(s for s in sensors if s["sensor"] is None)
    assert purged["records"] == 1 and purged["last"] is None and purged["series"] == []


def test_history_is_newest_first_with_a_wipe_and_a_swarm_move(mesh, admin, db_exec, home):
    z3 = mesh.member("z3-r0")
    home.add(z3)
    other = Home(mesh, db_exec, name="rogue-one")
    other.add(z3)  # a move
    z3.append("event", summary="test data")
    z3.append("lesson", summary="go left at the wall")
    z3.flush()
    r = admin.post(f"/api/admin/members/{z3.member_id}/wipe", json={"confirm": "z3-r0"})
    assert r.status_code == 200, r.text
    r = admin.request("DELETE", "/api/admin/records/z3-r0/1", json={"reason": "test data"})
    assert r.status_code == 200, r.text

    events = _get(admin, "history", member="z3-r0")["events"]
    stamps = [e["ts"] for e in events]
    assert stamps == sorted(stamps, reverse=True)
    titles = [e["title"] for e in events]
    assert titles[0] == "Deleted record z3-r0:1" and events[0]["kind"] == "admin" and events[0]["detail"] == "test data"
    assert "Memory wiped" in titles
    assert "Moved to rogue-one" in titles and "Joined echo-base" in titles
    first = next(e for e in events if e["kind"] == "learning")
    assert first["title"] == "First lesson" and first["detail"] == "go left at the wall"
    assert {"membership", "admin", "learning"} <= {e["kind"] for e in events}

    swarm = _get(admin, "history", swarm="rogue-one")["events"]
    assert any(e["title"] == "z3-r0's first lesson" for e in swarm)


@pytest.mark.parametrize("path", ["senses", "history"])
def test_subject_validation(path, mesh, admin, http):
    mesh.member("z3-r0")
    for params, status, code in (
        ({}, 422, "bad_subject"),
        ({"member": "z3-r0", "swarm": "echo-base"}, 422, "bad_subject"),
        ({"member": "nobody"}, 404, "not_found"),
    ):
        r = admin.get(f"/api/admin/profile/{path}", params=params)
        assert r.status_code == status and r.json()["error"]["code"] == code, (path, params)
    r = http.get(f"/api/admin/profile/{path}", params={"member": "z3-r0"}, headers={"x-ltms-admin": "1"})
    assert r.status_code == 401
