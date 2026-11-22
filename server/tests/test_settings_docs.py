"""One list of settings (#390): docs, compose.yaml and the example env files are
generated from server/config.py and must not drift; Compose passes every LTMS
setting in .env to the ltms container, and no other service's secrets."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
import types

import pytest
import yaml

from scripts import dump_settings as ds
from server.config import ServerConfig
from server.services import system_status

# Compose-only values in .env that aren't LTMS settings.
COMPOSE_ONLY = {"LTMS_IMAGE", "LTMS_PORT", "LTMS_BIND"}


def test_generated_files_are_current():
    for path, text in ds.outputs().items():
        assert path.read_text() == text, f"{path.name} is out of date: run python -m scripts.dump_settings"


def test_every_setting_is_documented():
    docs = ds.DOCS.read_text()
    missing = [f for f in ServerConfig.model_fields if f"| `{ds.env_name(f)}` |" not in docs]
    assert missing == []
    assert "| Docker Compose | `.env` next to `compose.yaml`" in docs
    assert "| systemd service | `/etc/ltms/ltms.env`" in docs
    undescribed = [s["env"] for s in ds.settings() if not s["about"]]
    assert undescribed == [], "add a comment in server/config.py or an ABOUT entry in scripts/dump_settings.py"


def _ltms_env() -> dict:
    return yaml.safe_load(ds.COMPOSE.read_text())["services"]["ltms"]["environment"]


def test_compose_passes_every_setting_and_no_other_secrets():
    env = _ltms_env()
    expected = {ds.env_name(f) for f in ServerConfig.model_fields if f not in ds.COMPOSE_SKIP}
    assert expected - set(env) == set()
    assert not [k for k in env if not (k.startswith("LTMS_") or k == "FORWARDED_ALLOW_IPS")]
    assert "LTMS_DB_PASSWORD" not in env  # only inside LTMS_DATABASE_URL
    for k in env:
        if k not in ("LTMS_DATABASE_URL",):
            assert "LRSQL_" not in str(env[k]) and "POSTGRES_" not in str(env[k])


def test_env_names_in_examples_are_real_settings():
    known = {ds.env_name(f) for f in ServerConfig.model_fields} | COMPOSE_ONLY
    for path in (ds.COMPOSE_ENV, ds.SERVICE_ENV, ds.COMPOSE):
        unknown = set(re.findall(r"\b(LTMS_[A-Z0-9_]+)", path.read_text())) - known
        assert unknown == set(), f"{path.name}: {unknown}"


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI (compose config only, no daemon)")
def test_compose_config_passes_new_settings_from_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ds.COMPOSE_ENV.read_text() + "\nLTMS_REFERENCE_REFRESH_HOURS=6\nLTMS_CHARTER_GRACE_DAYS=3\n")
    try:
        out = subprocess.run(
            ["docker", "compose", "-f", str(ds.COMPOSE), "--env-file", str(env_file),
             "--profile", "lrs", "config", "--format", "json"],
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"docker compose unavailable: {exc}")
    if out.returncode != 0 and "is not a docker command" in (out.stderr or ""):
        pytest.skip("docker compose plugin not installed")
    assert out.returncode == 0, out.stderr
    services = json.loads(out.stdout)["services"]
    ltms = services["ltms"]["environment"]
    assert ltms["LTMS_REFERENCE_REFRESH_HOURS"] == "6" and ltms["LTMS_CHARTER_GRACE_DAYS"] == "3"
    assert ltms["LTMS_GEOIP_DB"] == ""  # unset: empty, so LTMS uses its default
    assert not [k for k in ltms if k.startswith("LRSQL_")]
    assert "change-me" == services["lrs"]["environment"]["LRSQL_ADMIN_PASS_DEFAULT"]
    assert "change-me" not in [v for k, v in ltms.items() if k not in (
        "LTMS_DATABASE_URL", "LTMS_ADMIN_USERNAME", "LTMS_ADMIN_PASSWORD")]


def test_empty_compose_values_use_the_defaults(monkeypatch):
    """Compose passes unset settings as "" (${VAR:-}); LTMS must treat that as unset."""
    from server.config import load_config
    for name in ("LTMS_REFERENCE_REFRESH_HOURS", "LTMS_CHARTER_GRACE_DAYS", "LTMS_GEOIP_DB", "LTMS_ENABLE_SCHEDULER"):
        monkeypatch.setenv(name, "")
    cfg = load_config()
    assert cfg.reference_refresh_hours == 168 and cfg.charter_grace_days == 14
    assert cfg.geoip_db_path is None and cfg.enable_scheduler is True


def test_image_has_geoip_support():
    """The image installs the base dependencies; GeoIP's reader is one of them (#388)."""
    deps = tomllib.loads((ds.ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(re.match(r"maxminddb\b", d) for d in deps)
    dockerfile = (ds.ROOT / "Dockerfile").read_text()
    assert re.search(r'pip install --no-cache-dir "\.(\[[a-z,]+\])?"', dockerfile)  # the package, so its dependencies
    assert "ENV LTMS_DEPLOYMENT=docker" in dockerfile


def test_deployment_mode(tmp_path):
    cfg = types.SimpleNamespace(deployment="")
    assert system_status.deployment_mode(cfg, root=str(tmp_path), environ={}) == "other"
    assert system_status.deployment_mode(cfg, root=str(tmp_path), environ={"INVOCATION_ID": "x"}) == "service"
    (tmp_path / ".dockerenv").write_text("")
    assert system_status.deployment_mode(cfg, root=str(tmp_path), environ={"INVOCATION_ID": "x"}) == "docker"
    assert system_status.deployment_mode(types.SimpleNamespace(deployment="Service"), root=str(tmp_path),
                                         environ={}) == "service"
    assert system_status.deployment_mode(types.SimpleNamespace(deployment="bogus"), root="/nonexistent",
                                         environ={}) == "other"


def test_system_status_says_where_settings_live(app, admin):
    app.state.config = app.state.config.model_copy(update={"deployment": "docker"})
    body = admin.get("/api/admin/system").json()
    assert body["server"]["deployment"] == {"mode": "docker", "label": "Docker",
                                            "settings_file": ".env next to compose.yaml",
                                            "apply": "docker compose up -d"}
    assert "deployment: docker (settings: .env next to compose.yaml)" in body["diagnostics"]


def test_settings_page_gets_the_detected_deployment(app, admin):
    """Settings names the file for this deployment, so it must get the detected mode (#390 follow-up)."""
    assert admin.get("/api/admin/settings").json()["deployment"] in ("docker", "service", "other")
    app.state.config = app.state.config.model_copy(update={"deployment": "service"})
    assert admin.get("/api/admin/settings").json()["deployment"] == "service"
