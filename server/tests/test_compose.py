"""compose.yaml: every service survives a Docker daemon restart (#437).

A Docker package upgrade or a host reboot restarts the daemon. Without a
restart policy the containers stay down and the reverse proxy answers 502.
Container logs are capped so a chatty member can't fill the disk.
"""
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"
SERVICES = {"db", "ltms", "mqtt", "lrs-db", "lrs"}


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def test_all_known_services_present():
    assert SERVICES <= set(_services())


def test_every_service_restarts_with_the_daemon():
    for name, svc in _services().items():
        assert svc.get("restart") == "unless-stopped", name


def test_every_service_caps_its_logs():
    for name, svc in _services().items():
        logging = svc.get("logging") or {}
        assert logging.get("driver") == "json-file", name
        opts = logging.get("options") or {}
        assert opts.get("max-size") == "10m", name
        assert opts.get("max-file") == "3", name


def test_servers_wait_for_healthy_databases():
    services = _services()
    for app, db in (("ltms", "db"), ("lrs", "lrs-db")):
        assert services[app]["depends_on"][db]["condition"] == "service_healthy"
        assert "pg_isready" in " ".join(services[db]["healthcheck"]["test"])
