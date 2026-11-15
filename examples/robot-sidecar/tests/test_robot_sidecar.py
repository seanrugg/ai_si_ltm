"""Robot sidecar (#312): every adapter item is exactly one record; body state is one record per window."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from adapters.base import Adapter, Item
from adapters.vector_sdk import StateTracker, VectorSDK
from adapters.wirepod import WirePodLogs, parse_lines
from sidecar import BodyWindow, Sidecar, parse_models


class FakeLink:
    def __init__(self):
        self.records = []

    def append(self, type, *, payload=None, **header):
        self.records.append({"type": type, "payload": payload, **header})
        return self.records[-1]


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


def test_body_state_is_one_record_per_window():
    sc = Sidecar([], body_seconds=30, now=0)
    link = FakeLink()
    for t, head in ((1, 0.1), (2, 0.5), (3, 0.3)):
        sc.emit(Item("body", values={"head_angle_rad": head, "is_on_charger": True}))
        sc.poll(link, t)
    assert link.records == []
    sc.poll(link, 30)
    (rec,) = link.records
    assert rec["type"] == "perception" and rec["tags"] == ["internal", "telemetry", "body"]
    assert rec["payload"] == {"kind": "body", "samples": 3, "values": {
        "head_angle_rad": {"min": 0.1, "max": 0.5, "last": 0.3}, "is_on_charger": {"min": 1.0, "max": 1.0, "last": 1.0}}}
    sc.poll(link, 60)
    assert len(link.records) == 1  # an empty window signs nothing


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
    v.on_state(got.append, {"is_picked_up": False, "is_on_charger": True}, False, {"head_angle_rad": 0.2})
    v.on_state(got.append, {"is_picked_up": True, "is_on_charger": True}, True, {"head_angle_rad": 0.3})
    records = [(i.type, i.summary) for i in got if i.kind == "record"]
    assert records == [("event", "wake word"), ("event", "user intent: greeting_hello"), ("perception", "face seen"),
                       ("event", "picked up on"), ("event", "touch on")]
    assert [i.values for i in got if i.kind == "body"] == [{"head_angle_rad": 0.2}, {"head_angle_rad": 0.3}]
    t = StateTracker()
    assert t.changes({"a": True}) == [] and t.changes({"a": True}) == [] and t.changes({"a": False}) == [("a", False)]


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
    assert body["tags"] == ["internal", "telemetry", "body"]
    assert set(body["payload"]["values"]) == {"head_angle_rad"}  # no pose fields


def test_no_pose_record_when_nothing_moved():
    sc = Sidecar([], body_seconds=30, now=0)
    link = FakeLink()
    for i in range(5):
        sc.emit(Item("body", values=_pose(100, 50, 0.3, head_angle_rad=0.1)), at=1000.0 + i)
    sc.poll(link, 30)
    assert [r["tags"] for r in link.records] == [["internal", "telemetry", "body"]]
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

    from adapters.vector_sdk import body_values

    rot = NS(angle_z=NS(radians=0.5))
    robot = NS(pose=NS(position=NS(x=1.0, y=2.0, z=3.0), rotation=rot, origin_id=7), head_angle_rad=0.2)
    v = body_values(robot)
    assert v["pose_frame"] == "vector-odom:7" and v["pose_x_mm"] == 1.0 and v["pose_angle_rad"] == 0.5
    robot.pose = NS(position=NS(x=1.0, y=2.0, z=3.0), rotation=rot)  # an SDK without origin_id
    assert body_values(robot)["pose_frame"] == "vector-odom"
