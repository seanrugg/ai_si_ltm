"""System status (#383): every section, migration warning, job errors, fleet key,
and nothing secret in the response or the diagnostics text."""
from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import text

from server.services import fleet_key, system_status
from server.tests.conftest import ADMIN_PASS


@pytest.fixture(autouse=True)
def data_dir(app, tmp_path):
    app.state.config.data_dir = str(tmp_path / "data")


SECRET_KEY_NAMES = re.compile(r"(?i)(secret|password|passwd|private|token_hash|api[_-]?key|environ|^env$|cookie|session_key)")


def _keys(obj, out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


def test_every_section_and_admin_only(admin, http):
    body = admin.get("/api/admin/system").json()
    assert set(body) >= {"generated_at", "server", "database", "jobs", "keys", "integrations", "diagnostics"}
    s = body["server"]
    assert s["version"] and s["started_at"] and s["uptime_seconds"] is not None
    assert s["database"] in ("sqlite", "postgresql")
    assert s["data_dir"]["free_bytes"] > 0 and set(s["data_dir"]) == {"free_bytes", "total_bytes"}
    assert body["database"]["head"]
    assert body["jobs"]["scheduler_running"] is False  # tests run without the scheduler
    assert set(body["integrations"]) == {"lrs", "mcp", "reference_sources"}
    assert body["keys"]["swarms_with_leader"] == 0
    http.cookies.clear()
    assert http.get("/api/admin/system").status_code == 401


def test_migration_mismatch_is_a_warning(admin, db_exec):
    db_exec(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db_exec(text("INSERT INTO alembic_version (version_num) VALUES ('0000_old')"))
    db = admin.get("/api/admin/system").json()["database"]
    assert db["managed"] is True and db["current"] == "0000_old"
    assert db["pending"] is True and db["warning"].startswith("migrations pending")

    db_exec(text(f"UPDATE alembic_version SET version_num = '{system_status.code_head()}'"))
    db = admin.get("/api/admin/system").json()["database"]
    assert db["pending"] is False and db["warning"] is None


def test_unmanaged_schema_is_not_pending(admin):
    db = admin.get("/api/admin/system").json()["database"]
    assert db["managed"] is False and db["pending"] is False


def test_a_job_error_is_shown_scrubbed(app, admin):
    system_status.record_job(app.state, "purge_payloads")
    system_status.record_job(app.state, "lrs_forward", RuntimeError(
        "POST https://user:pw@lrs.example.org/xapi failed from 10.1.2.3 reading /opt/ltms/data/x.json\nsecond line"))
    jobs = {j["name"]: j for j in admin.get("/api/admin/system").json()["jobs"]["jobs"]}
    assert jobs["purge_payloads"]["result"] == "ok"
    bad = jobs["lrs_forward"]
    assert bad["result"] == "error" and bad["label"] == "LRS sync and summary"
    assert bad["message"].startswith("RuntimeError: POST <url> failed from <ip> reading <path>")
    for leak in ("pw@", "lrs.example.org", "10.1.2.3", "/opt/ltms", "second line"):
        assert leak not in bad["message"]


def test_fleet_key_fingerprint_never_private_bytes(app, admin):
    assert admin.get("/api/admin/system").json()["keys"]["fleet_key"]["present"] is False  # reported, never created
    cfg = app.state.config
    key = fleet_key.load(cfg)
    secret_hex = bytes(key).hex()
    body = admin.get("/api/admin/system")
    fk = body.json()["keys"]["fleet_key"]
    assert fk["present"] is True and fk["created_at"]
    assert fk["fingerprint"] == system_status.fingerprint(bytes(key.verify_key))
    assert "fleet-key.json" in fk["backup_note"]
    raw = body.text
    assert secret_hex not in raw and secret_hex.upper() not in raw
    assert json.loads(fleet_key.path_for(cfg).read_text())["secret_key"] not in raw


def test_no_secret_looking_fields_or_values(app, admin):
    fleet_key.load(app.state.config)
    body = admin.get("/api/admin/system")
    data = body.json()
    bad = [k for k in _keys(data) if SECRET_KEY_NAMES.search(k)]
    assert bad == []
    cfg = app.state.config
    raw = body.text
    for value in (ADMIN_PASS, cfg.resolved_database_url, cfg.data_dir, str(fleet_key.path_for(cfg))):
        assert value not in raw


def test_diagnostics_text_has_no_secrets(app, admin):
    key = fleet_key.load(app.state.config)
    system_status.record_job(app.state, "geoip_refresh", ValueError("token=abcdef0123456789abcdef0123456789 at 192.168.1.20"))
    diag = admin.get("/api/admin/system").json()["diagnostics"]
    assert diag.startswith("LTMS system status\n")
    for section in ("version:", "migrations:", "scheduler:", "job geoip_refresh: error", "fleet key: present", "LRS endpoints:"):
        assert section in diag
    cfg = app.state.config
    for leak in (bytes(key).hex(), ADMIN_PASS, cfg.resolved_database_url, cfg.data_dir,
                 "abcdef0123456789abcdef0123456789", "192.168.1.20"):
        assert leak not in diag


def test_scrub_unit():
    assert system_status.scrub(None) is None
    assert system_status.scrub("") == ""
    assert system_status.scrub("password: hunter2") == "password=<redacted>"
    assert len(system_status.scrub("word " * 100)) == system_status.MAX_MESSAGE
