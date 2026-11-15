"""Profile episodes: lanes, pulses, series and heartbeat derived at read time (#401)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from server.services import episodes as episodes_service

T0 = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
RANGE = {"since": T0.isoformat(), "until": (T0 + timedelta(seconds=600)).isoformat()}


def _at(s: float) -> str:
    return (T0 + timedelta(seconds=s)).isoformat()


def _get(admin, **params):
    r = admin.get("/api/admin/profile/episodes", params={**RANGE, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _body_window(robot, s, speed, volts=4.1):
    """A body window as sent before #408."""
    return robot.append("perception", summary="body state", tags=["internal", "telemetry", "body"], ts=_at(s),
                        payload={"kind": "body", "samples": 30, "values": {
                            "battery_volts": {"min": volts, "max": volts, "last": volts},
                            "left_wheel_speed_mmps": {"min": -speed, "max": 0, "last": 0},
                            "right_wheel_speed_mmps": {"min": 0, "max": speed / 2, "last": 0},
                            "pose_x_mm": {"min": 0, "max": 1, "last": 1}}})


def _flag(robot, s, name, onoff):
    return robot.append("event", summary=f"{name} {onoff}", tags=["body", "state-change"], ts=_at(s),
                        payload={"flag": "is_" + name.replace(" ", "_"), "value": onoff == "on"})


def _session(robot):
    """A short robot session in today's tagging (before #408)."""
    _flag(robot, 5, "on charger", "off")              # was on the charger before the range
    _body_window(robot, 30, 0)                        # still
    _body_window(robot, 60, 20)                       # nudging
    _body_window(robot, 90, 120)                      # driving
    _flag(robot, 100, "picked up", "on")
    _flag(robot, 101, "being held", "on")
    _flag(robot, 120, "picked up", "off")
    _flag(robot, 121, "being held", "off")            # one held episode, 100..121
    _flag(robot, 130, "cliff detected", "on")
    _flag(robot, 133, "cliff detected", "off")
    robot.append("perception", summary="face seen", tags=["vision", "face"], ts=_at(140),
                 payload={"face_id": 1, "name": "Alex"})
    robot.append("perception", summary="face seen", tags=["vision", "face"], ts=_at(150),
                 payload={"face_id": 4, "name": None})
    robot.append("perception", summary="face seen", tags=["vision", "face"], ts=_at(160),
                 payload={"face_id": -1, "name": None})
    robot.append("event", summary="wake word", tags=["voice", "wake-word"], ts=_at(200))
    robot.append("perception", summary="heard: tell me a story", tags=["voice", "heard"], ts=_at(203))
    robot.append("event", summary="LLM request", tags=["voice", "llm-request"], ts=_at(204))
    robot.append("communication", summary="Once upon a", tags=["voice", "reply", "llm"], ts=_at(214),
                 payload={"text": "x" * 150})
    robot.append("perception", summary="heartbeat (ping)", tags=["telemetry", "internal"], ts=_at(300))
    _flag(robot, 400, "on charger", "on")             # docks and stays: open-ended
    _flag(robot, 500, "falling", "on")                # never closed: runs to the end
    robot.append("event", summary="something new", tags=["mystery"], ts=_at(550))
    robot.flush()


def test_lanes_from_todays_tags(mesh, admin):
    robot = mesh.member("rover-1")
    _session(robot)
    out = _get(admin, member="rover-1")
    lanes = out["lanes"]
    assert set(lanes) == {"mind", "voice", "attention", "body", "motion", "power", "behavior"}
    assert out["duration_s"] == 600 and out["truncated"] is False and out["count"] == 21

    assert lanes["body"] == [[100, 121, "held", "held"], [130, 133, "cliff", "cliff"],
                             [500, 600, "falling", "falling", {"open": True}]]
    assert lanes["power"] == [[0, 5, "charger", "on charger"], [5, 400, "battery", "on battery"],
                              [400, 600, "charger", "on charger", {"open": True}]]
    assert lanes["motion"] == [[0, 30, "still", "0 mm/s max"], [30, 60, "nudging", "20 mm/s max"],
                               [60, 90, "driving", "120 mm/s max"]]
    assert lanes["attention"] == [[140, 150, "known", "Alex"], [150, 160, "unknown", "face #4"],
                                  [160, 170, "unknown", "unknown"]]
    assert lanes["voice"] == [[200, 203, "listening", "wake word"], [203, 205, "heard", "tell me a story"],
                              [214, 224, "replying", "x" * 80, {"estimated": True}]]
    assert lanes["mind"] == [[204, 214, "thinking", "LLM request"]]
    assert lanes["behavior"] == []
    assert out["series"] == {"volts": [[30, 4.1], [60, 4.1], [90, 4.1]],
                             "wheel_mmps": [[30, 0], [60, 20], [90, 120]]}


def test_pulses_carry_category_source_and_transit(mesh, admin):
    robot = mesh.member("rover-1")
    _session(robot)
    received = datetime.now(timezone.utc)
    pulses = _get(admin, member="rover-1")["pulses"]
    assert [p[0] for p in pulses] == sorted(p[0] for p in pulses)
    by = {p[5]: p for p in pulses}
    t, transit, *rest = by["LLM request"]
    assert t == 204 and rest == ["event", "act", "stt", "LLM request"]
    expected = (received - (T0 + timedelta(seconds=204))).total_seconds()  # received_at - ts
    assert expected - 60 < transit <= expected
    assert by["body state"][3:5] == ["self", "imu"]
    assert by["heartbeat (ping)"][3:5] == ["self", "battery"]
    assert by["cliff detected on"][3:5] == ["act", "cliff"]
    assert by["picked up on"][4] == "imu" and by["on charger on"][4] == "battery"
    assert by["face seen"][3:5] == ["sense", "camera"]
    assert by["wake word"][4] == "mic" and by["heard: tell me a story"][3:5] == ["sense", "mic"]
    assert by["Once upon a"][3:5] == ["comm", "llm"]
    assert by["something new"][3:5] == ["act", "hub"]  # the fallback: no lane
    assert all(p[1] > 0 for p in pulses)  # sent in the past, received now


def test_episode_scheme_from_408(mesh, admin):
    robot = mesh.member("rover-2")
    start = robot.append("event", summary="held", tags=["body", "episode-start", "held"], ts=_at(10),
                         payload={"flags": ["is_picked_up", "is_being_held"]})
    robot.append("event", summary="held ended", tags=["body", "episode-end", "held"], ts=_at(40),
                 refs={"start": f"rover-2:{start['seq']}"}, payload={"duration_s": 30})
    robot.append("event", summary="cliff ended", tags=["body", "episode-end", "cliff"], ts=_at(50),
                 refs={"start": "rover-2:999999"}, payload={"duration_s": 4})  # start outside the range
    robot.append("event", summary="animating", tags=["episode-start", "animating"], ts=_at(60))
    robot.append("event", summary="animating ended", tags=["episode-end", "animating"], ts=_at(65),
                 payload={"duration_s": 5})
    robot.append("event", summary="wheels moving", tags=["episode-start", "wheels-moving"], ts=_at(70))
    robot.append("event", summary="on charger", tags=["episode-start", "on-charger"], ts=_at(300))
    robot.append("perception", summary="body health", tags=["internal", "telemetry", "body-health"],
                 ts=_at(90), payload={"battery_volts": 3.9, "battery_level": 2})
    robot.append("perception", summary="wheels", tags=["telemetry", "body"], ts=_at(90), payload={
        "kind": "sensor", "sensor": "wheels", "modality": "encoder", "unit": "mm/s",
        "stats": {"left": {"min": -60, "max": 10}, "right": {"min": 0, "max": 55}}})
    robot.append("perception", summary="imu", tags=["telemetry", "body"], ts=_at(90),
                 payload={"kind": "sensor", "sensor": "imu", "samples": [{"t": 0, "v": [0, 0, 9.8]}]})
    robot.append("perception", summary="pose", tags=["telemetry", "pose"], ts=_at(90),
                 payload={"kind": "pose", "x": 1, "y": 2})
    robot.flush()
    out = _get(admin, member="rover-2")
    lanes = out["lanes"]
    assert lanes["body"] == [[10, 40, "held", "held"], [46, 50, "cliff", "cliff"]]
    assert lanes["animating"] == [[60, 65, "animating", "animating"]]
    assert lanes["wheels"] == [[70, 600, "moving", "wheels moving", {"open": True}]]
    assert lanes["power"] == [[0, 300, "battery", "on battery"],
                              [300, 600, "charger", "on charger", {"open": True}]]
    assert lanes["motion"] == [[60, 90, "driving", "60 mm/s max"]]  # wheels group only
    assert out["series"] == {"volts": [[90, 3.9]], "wheel_mmps": [[90, 60]]}
    by = {p[5]: p for p in out["pulses"]}
    assert by["body health"][3:5] == ["self", "battery"]
    assert by["wheels"][3:5] == ["sense", "imu"] and by["pose"][3:5] == ["sense", "imu"]
    assert by["cliff ended"][4] == "cliff"


def test_optional_lanes_absent_without_episodes(mesh, admin):
    robot = mesh.member("rover-1")
    robot.append("event", summary="hello", ts=_at(10))
    robot.flush()
    out = _get(admin, member="rover-1")
    assert "animating" not in out["lanes"] and "wheels" not in out["lanes"]
    assert out["lanes"]["power"] == []  # nothing about power: no battery filler


def test_heartbeat_interval(mesh, admin):
    robot = mesh.member("rover-1")
    for s in (0, 100, 130, 160, 200, 230, 260):  # intervals 100, 30, 30, 40, 30, 30
        robot.append("perception", summary="heartbeat", tags=["telemetry", "internal"], ts=_at(s))
    robot.append("perception", summary="saw a cup", ts=_at(270))  # not a self-report
    robot.flush()
    out = _get(admin, member="rover-1")
    assert out["heartbeat"] == {"interval_s": 30, "last_t": 260}
    assert _get(admin, member="rover-1", since=_at(500))["heartbeat"] == {"interval_s": None, "last_t": None}


def test_truncated(mesh, admin, monkeypatch):
    monkeypatch.setattr(episodes_service, "MAX_RECORDS", 5)
    robot = mesh.member("rover-1")
    for s in range(7):
        robot.append("event", summary=f"e{s}", ts=_at(s))
    robot.flush()
    out = _get(admin, member="rover-1")
    assert out["truncated"] is True and out["count"] == 5
    assert [p[5] for p in out["pulses"]] == ["e0", "e1", "e2", "e3", "e4"]
    monkeypatch.setattr(episodes_service, "MAX_RECORDS", 7)
    assert _get(admin, member="rover-1")["truncated"] is False


def test_swarm_subject_keeps_members_apart(mesh, admin, db_exec):
    from server.tests.test_swarms import Home
    home = Home(mesh, db_exec, name="test-swarm")
    a, b = mesh.member("rover-1"), mesh.member("rover-2")
    home.add(a)
    home.add(b)
    _flag(a, 10, "picked up", "on")
    _flag(b, 20, "picked up", "off")  # another member's flag must not close rover-1's episode
    _flag(a, 30, "picked up", "off")
    a.flush()
    b.flush()
    out = _get(admin, swarm="test-swarm")
    assert out["subject"] == {"type": "swarm", "name": "test-swarm"}
    assert out["lanes"]["body"] == [[0, 20, "held", "held"], [10, 30, "held", "held"]]


def test_admin_only_and_validation(mesh, admin, http):
    mesh.member("rover-1")
    r = http.get("/api/admin/profile/episodes", params={"member": "rover-1"}, headers={"x-ltms-admin": "1"})
    assert r.status_code == 401
    for params, status, code in (
        ({}, 422, "bad_subject"),
        ({"member": "nobody"}, 404, "not_found"),
        ({"member": "rover-1", "since": "yesterday"}, 422, "bad_range"),
        ({"member": "rover-1", "since": _at(10), "until": _at(5)}, 422, "bad_range"),
    ):
        r = admin.get("/api/admin/profile/episodes", params=params)
        assert r.status_code == status and r.json()["error"]["code"] == code, params


@pytest.mark.parametrize("tags,kind", [
    (["internal", "telemetry", "body"], "body"),
    (["telemetry", "body"], "body"),
    (["internal", "telemetry", "body-health"], "health"),
    (["telemetry", "internal"], "self"),
    (["mystery"], None),
])
def test_classify(tags, kind):
    assert episodes_service.classify("perception", tags) == kind
