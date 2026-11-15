"""Robot sidecar (#312): every adapter item is exactly one record; body state is one record per window."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from adapters.base import Adapter, Item
from adapters.vector_sdk import EpisodeTracker, VectorSDK
from adapters.wirepod import WirePodLogs, parse_lines
from sidecar import BodyWindow, Sidecar, parse_models


class FakeLink:
    def __init__(self):
        self.records = []

    def append(self, type, *, payload=None, **header):
        self.records.append({"type": type, "payload": payload, **header})
        return {**self.records[-1], "agent": "bot-1", "seq": len(self.records)}


class FakeAdapter(Adapter):
    name = "fake"

    def __init__(self, items):
        self.items = items

    def start(self, emit):
        for i in self.items:
            emit(i)


def test_each_item_is_exactly_one_record_and_nothing_else():
    items = [Item("record", "perception", "heard: hello", ["voice", "heard"], {"transcript": "hello"}),
             Item("record", "event", "wake word", ["voice"]),
             Item("record", "communication", "Hi there", ["voice", "reply"], {"text": "Hi there"})]
    sc = Sidecar([FakeAdapter(items)], body_seconds=30, now=0)
    sc.start()
    link = FakeLink()
    assert sc.poll(link, 1) == 3
    assert [(r["type"], r["summary"]) for r in link.records] == [
        ("perception", "heard: hello"), ("event", "wake word"), ("communication", "Hi there")]
    assert link.records[0]["payload"] == {"transcript": "hello"} and link.records[0]["tags"] == ["voice", "heard"]
    assert sc.poll(link, 2) == 0 and len(link.records) == 3  # nothing invented between items


def test_body_window_is_a_health_self_report_plus_one_sensed_record_per_group():
    """#408 replaces #312's single internal body record (which counted all sensing as self-report)."""
    sc = Sidecar([], body_seconds=30, now=0)
    link = FakeLink()
    wheels = {"sensor": "wheels", "modality": "encoder", "unit": "mm/s"}
    head = {"sensor": "head", "modality": "angle", "unit": "rad"}
    for t, (left, h) in enumerate([(10.0, 0.1), (30.0, 0.5), (20.0, 0.3)]):
        sc.emit(Item("sample", values={"left": left, "right": left}, sensor=wheels), at=100.0 + t)
        sc.emit(Item("sample", values={"value": 0.2}, sensor=head), at=100.0 + t)  # unchanged: still sent
        sc.poll(link, t + 1)
    sc.emit(Item("health", values={"battery_volts": 3.9, "charging": False}), at=101.0)
    sc.poll(link, 5)
    assert link.records == []
    sc.poll(link, 30)
    head_rec, wheels_rec, health = link.records
    assert health["tags"] == ["internal", "telemetry", "body-health"]
    assert health["payload"] == {"kind": "body-health", "samples": 1, "values": {
        "battery_volts": {"min": 3.9, "max": 3.9, "last": 3.9}, "charging": {"min": 0.0, "max": 0.0, "last": 0.0}}}
    for rec in (head_rec, wheels_rec):
        assert rec["type"] == "perception" and rec["tags"] == ["telemetry", "body"]
        assert "internal" not in rec["tags"]
    w = wheels_rec["payload"]
    assert (w["kind"], w["sensor"], w["modality"], w["unit"]) == ("sensor", "wheels", "encoder", "mm/s")
    assert w["samples"] == [{"t": 0, "v": {"left": 10.0, "right": 10.0}}, {"t": 1, "v": {"left": 30.0, "right": 30.0}},
                            {"t": 2, "v": {"left": 20.0, "right": 20.0}}]
    assert w["stats"]["count"] == 3 and w["stats"]["left"] == {"min": 10.0, "max": 30.0, "mean": 20.0}
    h = head_rec["payload"]
    assert [x["v"] for x in h["samples"]] == [0.2, 0.2, 0.2] and h["stats"] == {"count": 3, "min": 0.2, "max": 0.2,
                                                                                   "mean": 0.2}
    sc.poll(link, 60)
    assert len(link.records) == 3  # an empty window signs nothing: a gap is silence


def test_items_are_checked():
    with pytest.raises(ValueError):
        Item("record", "decision", "decided")  # a sidecar never decides
    with pytest.raises(ValueError):
        Item("body", values={})
    assert BodyWindow().report() is None
    assert parse_models("qwen2.5:1.5b@escapepod, x") == [{"name": "qwen2.5:1.5b", "runs_on": "escapepod"},
                                                          {"name": "x", "runs_on": "bridge"}]


UI_LOG = """2026.11.12 21:00:00: Intent matched: intent_weather, transcribed text: 'old line', device: 00a1
2026.11.12 22:05:01: Intent matched: intent_imperative_dance, transcribed text: 'dance for me', device: 00a1
2026.11.12 22:05:01: Parameters sent: map[]
2026.11.12 22:05:09: LLM response for 00a1: Sure, here goes!
2026.11.12 22:05:10: Intent matched: intent_greeting_hello, transcribed text: 'hello', device: 00b2
2026.11.12 22:05:11: Lua error: unable to open image file: nope
"""
DEBUG_LOG = """2026.11.12 22:06:00: Bot 00a1 Custom Intent Matched: Status Report - Vector gives a status report - intent_greeting_hello
2026.11.12 22:06:30: Making LLM request for device 00a1...
2026.11.12 22:06:31: Bot 00a1 request served.
"""


def test_wirepod_lines_map_one_to_one(tmp_path):
    health = tmp_path / "hub-health.json"
    health.write_text(json.dumps({"at": 1794538000.5, "cpu_pct": 4}))
    logs = {"/api/get_logs": UI_LOG, "/api/get_debug_logs": DEBUG_LOG}
    wp = WirePodLogs("http://pod:8080", esn="00a1", status_intent="Status Report", health_state=str(health),
                     fetch=lambda url: logs[url.replace("http://pod:8080", "")], started=datetime(2026, 11, 12, 22, 0))
    got = []
    assert wp.poll_once(got.append) == 5
    assert [(i.type, i.summary, i.tags) for i in got] == [
        ("perception", "heard: dance for me", ["voice", "heard"]),
        ("event", "intent parameters", ["voice", "intent-params"]),
        ("communication", "Sure, here goes!", ["voice", "reply", "llm"]),
        ("event", "custom intent: Status Report", ["voice", "custom-intent", "status-report"]),
        ("event", "LLM request", ["voice", "llm-request"]),
    ]
    assert got[0].payload["intent"] == "intent_imperative_dance" and got[0].payload["device"] == "00a1"
    assert got[3].payload["health_reading_at"] == 1794538000.5
    # The logs are rolling buffers: polling again finds nothing new.
    assert wp.poll_once(got.append) == 0
    assert len(parse_lines("garbage\n" + UI_LOG)) == 6


def test_vector_events_map_one_to_one():
    v = VectorSDK(face_gap_seconds=10)
    got = []
    v.on_wake_word(got.append)
    v.on_user_intent(got.append, "greeting_hello", "{}")
    v.on_face(got.append, 3, "Sean", "HAPPINESS", now=0)
    v.on_face(got.append, 3, "Sean", "HAPPINESS", now=5)  # same face within the gap
    records = [(i.type, i.summary) for i in got if i.kind == "record"]
    assert records == [("event", "wake word"), ("event", "user intent: greeting_hello"), ("perception", "face seen")]
    t = EpisodeTracker(debounce=0)
    assert t.update({"is_falling": True}, 0) == ([], {})  # first reading: no event
    assert t.update({"is_falling": False}, 1) == ([("end", "falling", 1, ["is_falling"], None)], {})


# -- pose (#407) -----------------------------------------------------------------

def _pose(x, y, theta=0.0, frame="vector-odom:1", **more):
    return {"pose_x_mm": x, "pose_y_mm": y, "pose_z_mm": 0.0, "pose_angle_rad": theta, "pose_frame": frame, **more}


def test_a_window_with_moves_is_one_pose_record_plus_a_body_record_without_pose():
    sc = Sidecar([], body_seconds=30, now=0)
    link = FakeLink()
    t0 = 1_794_000_000.0
    for i, (x, y) in enumerate([(0, 0), (2, 0), (20, 0), (20.5, 0), (40, 10)]):
        sc.emit(Item("body", values=_pose(x, y, head_angle_rad=0.1)), at=t0 + i)
    sc.poll(link, 30)
    pose, body = link.records
    assert pose["type"] == "perception" and pose["tags"] == ["telemetry", "pose"]
    assert "internal" not in pose["tags"]
    assert pose["ts"].startswith("2026-") and pose["ts"].endswith("Z")
    p = pose["payload"]
    assert p["kind"] == "pose" and p["frame"] == "vector-odom:1" and p["unit"] == "mm"
    # 2 mm and 0.5 mm moves are below the 5 mm threshold; the last sample is always kept.
    assert [(s["t"], s["x"], s["y"]) for s in p["samples"]] == [(0, 0, 0), (2, 20, 0), (4, 40, 10)]
    assert all("theta" in s for s in p["samples"])
    assert body["tags"] == ["telemetry", "body"]  # #408: the rest is a sensed group, not a self-report
    assert body["payload"]["samples"][0]["v"] == {"head_angle_rad": 0.1}  # no pose fields


def test_no_pose_record_when_nothing_moved():
    sc = Sidecar([], body_seconds=30, now=0)
    link = FakeLink()
    for i in range(5):
        sc.emit(Item("body", values=_pose(100, 50, 0.3, head_angle_rad=0.1)), at=1000.0 + i)
    sc.poll(link, 30)
    assert [r["tags"] for r in link.records] == [["telemetry", "body"]]
    # Still still in the next window, compared with where the last window ended.
    sc.emit(Item("body", values=_pose(101, 50, 0.3)), at=1031.0)
    sc.poll(link, 60)
    assert len(link.records) == 1


def test_a_turn_alone_is_movement():
    sc = Sidecar([], body_seconds=30, now=0)
    link = FakeLink()
    sc.emit(Item("body", values=_pose(0, 0, 0.0)), at=0.0)
    sc.emit(Item("body", values=_pose(0, 0, 0.1)), at=1.0)  # about 5.7 degrees
    sc.poll(link, 30)
    (pose,) = link.records
    assert [s["theta"] for s in pose["payload"]["samples"]] == [0.0, 0.1]


def test_an_origin_change_starts_a_new_frame():
    sc = Sidecar([], body_seconds=30, now=0)
    link = FakeLink()
    sc.emit(Item("body", values=_pose(0, 0, frame="vector-odom:1")), at=0.0)
    sc.emit(Item("body", values=_pose(50, 0, frame="vector-odom:1")), at=1.0)
    sc.emit(Item("body", values=_pose(0, 0, frame="vector-odom:2")), at=2.0)  # picked up and put down
    sc.emit(Item("body", values=_pose(0, 80, frame="vector-odom:2")), at=3.0)
    sc.poll(link, 30)
    frames = [(r["payload"]["frame"], [(s["x"], s["y"]) for s in r["payload"]["samples"]]) for r in link.records]
    assert frames == [("vector-odom:1", [(0, 0), (50, 0)]), ("vector-odom:2", [(0, 0), (0, 80)])]
    assert all(r["tags"] == ["telemetry", "pose"] for r in link.records)


def test_vector_pose_frame_comes_from_the_origin_id():
    from types import SimpleNamespace as NS

    from adapters.vector_sdk import pose_values as body_values  # #408: pose has its own reader

    rot = NS(angle_z=NS(radians=0.5))
    robot = NS(pose=NS(position=NS(x=1.0, y=2.0, z=3.0), rotation=rot, origin_id=7), head_angle_rad=0.2)
    v = body_values(robot)
    assert v["pose_frame"] == "vector-odom:7" and v["pose_x_mm"] == 1.0 and v["pose_angle_rad"] == 0.5
    robot.pose = NS(position=NS(x=1.0, y=2.0, z=3.0), rotation=rot)  # an SDK without origin_id
    assert body_values(robot)["pose_frame"] == "vector-odom"


# -- episodes, chain, models, specs (#408) -------------------------------------

def _signed(items, link=None):
    sc = Sidecar([], body_seconds=30, now=0)
    link = link or FakeLink()
    for i in items:
        sc.emit(i)
    sc.poll(link, 1)
    return link.records


def test_pickup_is_one_episode_with_refs_and_duration():
    v = VectorSDK()
    got = []
    v.on_flags(got.append, {"is_picked_up": False, "is_being_held": False}, None, 1000.0)
    v.on_flags(got.append, {"is_picked_up": True, "is_being_held": False}, None, 1001.0)
    v.on_flags(got.append, {"is_picked_up": True, "is_being_held": True}, None, 1001.6)  # confirmed
    v.on_flags(got.append, {"is_picked_up": False, "is_being_held": True}, None, 1005.0)
    v.on_flags(got.append, {"is_picked_up": False, "is_being_held": False}, None, 1007.0)
    v.on_flags(got.append, {"is_picked_up": False, "is_being_held": False}, None, 1007.5)  # confirmed
    recs = _signed(got)
    assert [r["summary"] for r in recs] == ["held started", "held ended"]
    start, end = recs
    assert start["tags"] == ["body", "episode-start", "held"] and end["tags"] == ["body", "episode-end", "held"]
    assert start["payload"]["flags"] == ["is_picked_up"]
    assert end["payload"]["flags"] == ["is_being_held", "is_picked_up"]
    assert end["refs"] == {"start": "bot-1:1"} and end["payload"]["duration_s"] == 6.0
    assert start["ts"] == "1970-01-01T00:16:41.000Z"
    assert end["ts"] == "1970-01-01T00:16:47.000Z"  # when it really ended, not when confirmed


def test_a_flicker_under_half_a_second_is_no_episode_but_is_counted():
    v = VectorSDK()
    got = []
    v.on_flags(got.append, {"is_cliff_detected": False}, False, 0.0)
    v.on_flags(got.append, {"is_cliff_detected": True}, True, 0.1)
    v.on_flags(got.append, {"is_cliff_detected": False}, False, 0.4)
    assert [i.kind for i in got] == ["flicker"]
    assert got[0].values == {"cliff": 1, "touch": 1}
    sc = Sidecar([], body_seconds=30, now=0)
    link = FakeLink()
    for i in got:
        sc.emit(i)
    sc.poll(link, 30)
    (health,) = link.records
    assert health["tags"] == ["internal", "telemetry", "body-health"]
    assert health["payload"]["stats"] == {"flicker": {"cliff": 1, "touch": 1}}


def test_animating_and_wheels_moving_are_episodes():
    v = VectorSDK()
    got = []
    for now, anim, wheels in ((0.0, False, False), (1.0, True, False), (1.5, True, True), (2.2, True, True),
                              (3.0, False, True), (3.6, False, False), (4.2, False, False), (4.4, False, True),
                              (4.6, False, False)):
        v.on_flags(got.append, {"is_animating": anim, "are_wheels_moving": wheels}, None, now)
    recs = _signed([i for i in got if i.kind == "record"])
    assert [(r["summary"], r["tags"][-1]) for r in recs] == [
        ("animating started", "animating"), ("wheels-moving started", "wheels-moving"),
        ("animating ended", "animating"), ("wheels-moving ended", "wheels-moving")]
    assert recs[2]["refs"] == {"start": "bot-1:1"} and recs[2]["payload"]["duration_s"] == 2.0
    assert recs[3]["refs"] == {"start": "bot-1:2"} and recs[3]["payload"]["duration_s"] == 2.1
    assert all("animation" not in r["payload"] for r in recs)  # which animation: the SDK doesn't say
    assert [i.values for i in got if i.kind == "flicker"] == [{"wheels-moving": 1}]


def test_a_state_on_before_watching_ends_without_refs():
    v = VectorSDK(debounce_seconds=0)
    got = []
    v.on_flags(got.append, {"is_on_charger": True}, None, 0.0)
    v.on_flags(got.append, {"is_on_charger": False}, None, 5.0)
    (end,) = _signed(got)
    assert end["summary"] == "on-charger ended" and "refs" not in end and "duration_s" not in end["payload"]


def test_vector_sensor_groups_and_health_from_the_sdk():
    from types import SimpleNamespace as NS

    from adapters.vector_sdk import battery_values, sensor_values

    robot = NS(left_wheel_speed_mmps=12.0, right_wheel_speed_mmps=11.5, accel=NS(x=1.0, y=2.0, z=3.0),
               gyro=NS(x=0.1, y=0.2, z=0.3), head_angle_rad=0.4, lift_height_mm=32.0,
               touch=NS(last_sensor_reading=NS(raw_touch_value=4500, is_being_touched=False)),
               proximity=NS(last_sensor_reading=NS(distance=NS(distance_mm=180), signal_quality=0.8,
                                                   unobstructed=True, found_object=False, is_lift_in_fov=False)))
    g = sensor_values(robot)
    assert g["wheels"] == {"left": 12.0, "right": 11.5} and g["head"] == {"value": 0.4} and g["lift"] == {"value": 32.0}
    assert g["imu"]["gyro_z"] == 0.3 and g["touch"] == {"raw": 4500, "pressed": False}
    assert g["proximity"]["distance"] == 180 and g["proximity"]["unobstructed"] is True
    assert "proximity" not in sensor_values(NS(head_angle_rad=0.1))  # an SDK without it: not invented
    got = []
    VectorSDK().on_body(got.append, robot)
    assert {i.sensor["sensor"] for i in got if i.kind == "sample"} == {"wheels", "imu", "head", "lift", "touch",
                                                                       "proximity"}
    assert battery_values(NS(battery_volts=3.8, battery_level=2, is_charging=True, is_on_charger_platform=True)) == {
        "battery_volts": 3.8, "battery_level": 2, "charging": True, "on_charger_platform": True}


def test_specs_report_carries_animation_counts_and_no_lists():
    from types import SimpleNamespace as NS

    from adapters.vector_sdk import robot_specs

    robot = NS(anim=NS(anim_list=[f"anim_{i}" for i in range(7)], anim_trigger_list=["GreetingHello", "Dance"]),
               get_version_state=lambda: NS(os_version="2.0.1.6091oskr"))
    specs = robot_specs(robot, "0.8.1")
    assert specs["hardware"] == {"board": "Anki/DDL Vector"}
    assert specs["software"] == {"harness": "robot-sidecar", "harness_version": "wirepod_vector_sdk 0.8.1",
                                 "os": "Vector firmware 2.0.1.6091oskr"}
    assert "animations: 7" in specs["notes"] and "animation_triggers: 2" in specs["notes"]
    assert "anim_0" not in json.dumps(specs) and "Dance" not in json.dumps(specs)
    v = VectorSDK()
    sent = []
    v.on_specs = sent.append
    v.on_connected(robot)
    assert len(sent) == 1 and "animations: 7" in sent[0]["notes"]


CHAT_UI = """2026.11.14 10:00:00: Intent matched: intent_greeting_hello, transcribed text: 'what is a robot', device: 00a1
2026.11.14 10:00:03: LLM response for 00a1: A robot is a machine.
2026.11.14 10:05:00: LLM response for 00a1: Unprompted.
"""
CHAT_DEBUG = """2026.11.14 10:00:01: Making LLM request for device 00a1...
"""


def test_conversation_chain_is_linked_with_model_and_runs_on():
    logs = {"/api/get_logs": CHAT_UI, "/api/get_debug_logs": CHAT_DEBUG}
    wp = WirePodLogs("http://pod", esn="00a1", fetch=lambda url: logs[url.replace("http://pod", "")],
                     started=datetime(2026, 11, 14, 9, 0), model="gemma3:1b", runs_on="hub")
    got = []
    wp.poll_once(got.append)
    recs = _signed(got)
    assert [r["type"] for r in recs] == ["perception", "event", "communication", "communication"]
    heard, req, reply, lone = recs
    assert "refs" not in heard
    assert req["refs"] == {"because": "bot-1:1"}
    assert reply["refs"] == {"because": "bot-1:2"}
    assert reply["payload"]["model"] == "gemma3:1b" and reply["payload"]["runs_on"] == "hub"
    assert "refs" not in lone  # no request within 30 s
    assert wp.unmatched == {"llm-request": 0, "llm-reply": 1}
    assert WirePodLogs.from_env({"SIDECAR_COGNITION_MODELS": "qwen2.5:1.5b@pod-host, x"}).model == "qwen2.5:1.5b"
    bare = WirePodLogs("http://pod", fetch=lambda url: logs[url.replace("http://pod", "")],
                       started=datetime(2026, 11, 14, 9, 0))
    out = []
    bare.poll_once(out.append)
    assert "model" not in out[2].payload  # unknown: left out, never guessed


def test_an_empty_models_setting_logs_the_warning(caplog):
    from sidecar import cognition_models

    with caplog.at_level("WARNING"):
        assert cognition_models({"SIDECAR_COGNITION_MODELS": ""}) == []
    assert "Language models: not reported" in caplog.text and "SIDECAR_COGNITION_MODELS=<model>@<host>" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING"):
        assert cognition_models({"SIDECAR_COGNITION_MODELS": "m@h"}) == [{"name": "m", "runs_on": "h"}]
    assert caplog.text == ""
