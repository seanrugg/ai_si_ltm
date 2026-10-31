"""Tests for scripts/setup_env.py.

Runs the script's main(argv) against tmp_path .env files.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import stat
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
SCRIPT = _HERE.parent.parent / "scripts" / "setup_env.py"


def _load_setup_env():
    spec = importlib.util.spec_from_file_location("setup_env", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup_env = _load_setup_env()


def _parse_env(path: Path) -> dict:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        values[key.strip()] = val.strip()
    return values


def test_web_with_lrs_and_mqtt(tmp_path):
    out = tmp_path / ".env"
    rc = setup_env.main(
        ["--mode", "web", "--with-lrs", "--with-mqtt", "--out", str(out)]
    )
    assert rc == 0
    values = _parse_env(out)

    # No placeholder left anywhere.
    assert all("change-me" not in v for v in values.values())

    assert values["COMPOSE_PROFILES"] == "lrs,mqtt"
    assert values["LTMS_BIND"] == "127.0.0.1"
    assert values["LTMS_COOKIE_SECURE"] == "true"
    assert values["FORWARDED_ALLOW_IPS"] == "*"

    # Bootstrap credentials mirror the generated LRSQL key/secret.
    assert values["LTMS_LRS_BOOTSTRAP_USERNAME"] == values["LRSQL_API_KEY"]
    assert values["LTMS_LRS_BOOTSTRAP_SECRET"] == values["LRSQL_API_SECRET"]
    assert values["LTMS_LRS_BOOTSTRAP_URL"] == "http://lrs:8080/xapi"
    assert values["LTMS_LRS_BOOTSTRAP_ENABLED"] == "true"

    # LTMS_SECRET_KEY is a Fernet key: base64 of 32 bytes.
    assert len(base64.urlsafe_b64decode(values["LTMS_SECRET_KEY"])) == 32

    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_local_mode(tmp_path):
    out = tmp_path / ".env"
    setup_env.main(["--mode", "local", "--out", str(out)])
    values = _parse_env(out)

    assert values["LTMS_BIND"] == "0.0.0.0"
    assert values["LTMS_COOKIE_SECURE"] == "false"
    assert values["FORWARDED_ALLOW_IPS"] == "127.0.0.1"
    assert "COMPOSE_PROFILES" not in values
    assert values["LTMS_LRS_BOOTSTRAP_URL"] == ""


def test_existing_output_refused_without_force(tmp_path):
    out = tmp_path / ".env"
    out.write_text("KEEP ME\n", encoding="utf-8")
    before = out.read_text()
    rc = setup_env.main(["--mode", "local", "--out", str(out)])
    assert rc != 0
    assert out.read_text() == before


def test_two_runs_produce_different_passwords(tmp_path):
    out1 = tmp_path / "a.env"
    out2 = tmp_path / "b.env"
    setup_env.main(["--mode", "local", "--out", str(out1)])
    setup_env.main(["--mode", "local", "--out", str(out2)])
    v1 = _parse_env(out1)
    v2 = _parse_env(out2)
    assert v1["LTMS_DB_PASSWORD"] != v2["LTMS_DB_PASSWORD"]


def test_output_leaks_no_generated_secrets(tmp_path, capsys):
    out = tmp_path / ".env"
    setup_env.main(
        ["--mode", "web", "--with-lrs", "--with-mqtt", "--out", str(out)]
    )
    captured = capsys.readouterr()
    values = _parse_env(out)

    secret_keys = [
        key
        for key in values
        if "PASSWORD" in key or "SECRET" in key
    ] + ["LRSQL_API_KEY", "LTMS_SECRET_KEY"]
    for key in secret_keys:
        assert values[key] not in captured.out
        assert values[key] not in captured.err
