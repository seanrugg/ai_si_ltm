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
