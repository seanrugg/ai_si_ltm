"""The App Lab member's Python side (#299), off the board: no App Lab, no MCU.

``pytest examples/app-lab-member/tests``
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import app_member as am  # noqa: E402
from server.tests.conftest import admin, app, config, db_url, http, mesh  # noqa: E402,F401 - fixtures

READY = {"MEMBER_NAME": "board-1", "LTMS_SERVER": "https://ltms.test.invalid", "MQTT_HOST": "hub.test.invalid",
         "SWARM_TOPIC_ID": "sw-1a2b3c4d"}


def write_env(data: Path, **values) -> None:
    """The template, with values appended (a later line wins, as in an edited file)."""
    am.ensure_data_dir(data)
    with open(data / "member.env", "a", encoding="utf-8") as f:
        f.writelines(f"{k}={v}\n" for k, v in values.items())


# ------------------------------------------------------------------ settings
def test_template_is_written_once_private_and_generic(tmp_path):
    data = tmp_path / "data"
    assert am.ensure_data_dir(data) is True
    assert am.ensure_data_dir(data) is False
    env_file = data / "member.env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(data.stat().st_mode) == 0o700
    text = env_file.read_text()
    assert "MEMBER_NAME=\n" in text and "ENROLL=hold" in text
    assert "ltms.example.org" in text and "hub.local" in text  # placeholders only


def test_problems_until_configured(tmp_path):
    data = tmp_path / "data"
    am.ensure_data_dir(data)
    env = am.read_env_file(data / "member.env")
    todo = am.problems(env)
    assert "set MEMBER_NAME" in todo and any("LTMS_SERVER" in p for p in todo) and any("MQTT_HOST" in p for p in todo)
    assert am.problems({**env, **READY}) == []
    assert am.problems({**env, **READY, "MEMBER_NAME": "Board 1"}) != []
    assert am.problems({**env, **READY, "ROLE": "leader"}) == ["ROLE must be member or observer"]


def test_load_builds_the_member_settings(tmp_path):
    data = tmp_path / "data"
    write_env(data, **READY, MQTT_PASSWORD="s3cret", ENROLL="request", ENROLL_SWARM="lab-swarm", ROLE="observer")
    s = am.load(data, environ={})
    assert s.name == "board-1" and s.enroll and s.role == "observer" and s.enroll_swarm == "lab-swarm"
    me = s.member_env
    assert me["MQTT_USERNAME"] == "board-1" and me["MQTT_PASSWORD"] == "s3cret"
    assert me["LTMS_KEY_PATH"].endswith("keys/board-1.key.json") and me["MEMBER_OUTBOX_PATH"].startswith(str(data))
    from cli_agent.member_main import load_config
    cfg = load_config(me)
    assert cfg.swarm_topic_id == "sw-1a2b3c4d" and cfg.heartbeat_seconds == 600 and cfg.mqtt_host == "hub.test.invalid"
    assert am.load(data, environ={"MEMBER_NAME": "board-2"}).name == "board-2"  # the environment wins


def test_key_is_made_once_private_and_checked(tmp_path):
    data = tmp_path / "data"
    write_env(data, **READY)
    s = am.load(data, environ={})
    pub = am.ensure_key(s)
    assert len(pub) == 64 and am.ensure_key(s) == pub
    assert stat.S_IMODE(s.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(s.key_path.parent.stat().st_mode) == 0o700
    other = am.load(data, environ={"MEMBER_NAME": "board-2"})
    other.key_path.parent.mkdir(parents=True, exist_ok=True)
    other.key_path.write_text(s.key_path.read_text())  # board-1's key under board-2's name
    with pytest.raises(SystemExit):
        am.ensure_key(other)


def test_enroll_body(tmp_path):
    data = tmp_path / "data"
    write_env(data, **READY, ENROLL_SWARM="lab-swarm", INVITE="abc123", OPERATOR="An Operator")
    body = am.enroll_body(am.load(data, environ={}), "ab" * 32, "x" * 2000)
    assert body["intended"] == {"swarm": "lab-swarm", "role": "member"} and body["invite"] == "abc123"
    assert body["operator"] == "An Operator" and len(body["note"]) == 1000 and body["permissions"] == ["contribute"]


# ------------------------------------------------------------------ enrollment and approval
class FakeResp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, enroll_status=202, approved_after=1):
        self.posts, self.logins = [], 0
        self.enroll_status, self.approved_after = enroll_status, approved_after
        client = self

        class Http:
            def post(self, path, json):
                client.posts.append((path, json))
                return FakeResp(client.enroll_status, {"error": {"code": "enrollment_closed", "message": "no"}})
        self.http = Http()

    def login(self):
        from cli_agent.client import LTMSError
        self.logins += 1
        if self.logins <= self.approved_after:
            raise LTMSError(403, "pending", "not approved yet")
        return {"permissions": ["contribute"]}


def _settings(tmp_path, **extra):
    data = tmp_path / "data"
    write_env(data, **READY, **extra)
    return am.load(data, environ={})


def test_nothing_is_sent_until_enroll_is_requested(tmp_path):
    c = FakeClient()
    a = am.Approval(_settings(tmp_path), c, "ab" * 32, "note")
    a.once()
    assert a.phase == "awaiting-enroll" and c.posts == [] and c.logins == 0 and not a.ready.is_set()


def test_enroll_once_then_wait_for_approval(tmp_path):
    s = _settings(tmp_path, ENROLL="request")
    c = FakeClient(approved_after=1)
    a = am.Approval(s, c, "ab" * 32, "note")
    a.once()
    assert len(c.posts) == 1 and a.phase == "pending-approval" and not a.ready.is_set()
    a.once()
    assert len(c.posts) == 1 and a.phase == "approved" and a.ready.is_set()
    # a restart after approval goes straight to the member
    assert am.Approval(s, FakeClient(), "ab" * 32, "note").ready.is_set()


def test_a_known_name_is_not_an_error_and_a_refusal_stops(tmp_path):
    a = am.Approval(_settings(tmp_path, ENROLL="request"), FakeClient(enroll_status=409), "ab" * 32, "n")
    a.once()
    assert a.phase == "pending-approval"
    refused = FakeClient(enroll_status=403)
    b = am.Approval(_settings(tmp_path / "b", ENROLL="request"), refused, "ab" * 32, "n")
    b.run()  # returns: a refusal isn't retried in a loop
    assert b.phase == "enroll-failed" and "enrollment_closed" in b.detail and len(refused.posts) == 1


def test_enrollment_and_approval_against_the_server(tmp_path, http, admin):
    from cli_agent.client import LTMSClient
    s = _settings(tmp_path, ENROLL="request", MEMBER_KIND="robot")
    pub = am.ensure_key(s)
    client = LTMSClient(key_path=str(s.key_path), http=http)
    a = am.Approval(s, client, pub, am.hardware_note(pub))
    a.once()
    assert a.phase == "pending-approval" and not a.ready.is_set()
    pending = admin.get("/api/admin/members", params={"status": "pending"}).json()
    rows = pending.get("members", pending) if isinstance(pending, dict) else pending
    row = next(m for m in rows if m["name"] == "board-1")
    assert row["public_key"] == pub
    assert admin.post(f"/api/admin/members/{row['id']}/approve", json={"scope": None}).status_code == 200
    a.once()
    assert a.ready.is_set() and json.loads((s.data_dir / "approved.json").read_text())["permissions"]


# ------------------------------------------------------------------ MCU telemetry and link state
def test_telemetry_folds_status_lines_into_one_summary():
    t = [0.0]
    w = am.TelemetryWindow(60.0, clock=lambda: t[0])
    for i in range(5):
        t[0] = float(i)
        w.add(100 + i, 50 * i, 0, -1)
    assert not w.due()
    t[0] = 60.0
    assert w.due()
    s = w.take()
    assert s["samples"] == 5 and s["reset_cause"] == "unknown"
    assert s["values"]["uptime_s"] == {"min": 100.0, "max": 104.0, "last": 104.0}
    t[0] = 125.0
    quiet = w.take()
    assert quiet["samples"] == 0 and quiet["silent_s"] == 121.0
    w.add(1, 1, 0, 3)
    assert w.take()["reset_cause"] == 3


def test_link_state():
    assert am.link_state("needs-settings", None) == am.LINK_PENDING
    assert am.link_state("pending-approval", None) == am.LINK_PENDING
    assert am.link_state("starting", None) == am.LINK_STARTING
    assert am.link_state("enroll-failed", None) == am.LINK_OFFLINE
    assert am.link_state("member", {"running": True, "mqtt": True}) == am.LINK_ONLINE
    assert am.link_state("member", {"running": True, "mqtt": False, "transport": "direct"}) == am.LINK_ONLINE
    assert am.link_state("member", {"running": True, "mqtt": False, "transport": "mqtt"}) == am.LINK_OFFLINE


def test_no_site_specific_values_in_the_app():
    """AGENTS.md rule 10: placeholders only, in code, settings and docs."""
    root = Path(__file__).resolve().parents[1]
    banned = ("cucorn", "echo-base", "10.0.0.", "q3-u3", "ventuno-q3")
    for f in [*root.rglob("*.py"), *root.rglob("*.ino"), *root.rglob("*.md"), *root.rglob("*.yaml"),
              *root.rglob("*.sh")]:
        if "tests" in f.parts:
            continue
        text = f.read_text(encoding="utf-8").lower()
        assert not any(b in text for b in banned), f"site-specific value in {f.name}"
