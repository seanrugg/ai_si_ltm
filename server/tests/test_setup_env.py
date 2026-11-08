"""Tests for scripts/setup_env.py.

Runs the script's main(argv) against tmp_path .env files.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import shutil
import stat
import subprocess
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
        ["--mode", "web", "--with-lrs", "--with-mqtt",
         "--public-url", "https://ltms.example.org", "--out", str(out)]
    )
    assert rc == 0
    values = _parse_env(out)

    # No placeholder left anywhere.
    assert all("change-me" not in v for v in values.values())

    assert values["COMPOSE_PROFILES"] == "lrs,mqtt"
    assert values["LTMS_BIND"] == "127.0.0.1"
    assert values["LTMS_COOKIE_SECURE"] == "true"
    assert values["FORWARDED_ALLOW_IPS"] == "*"
    assert values["LTMS_PUBLIC_BASE_URL"] == "https://ltms.example.org"

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
        ["--mode", "web", "--with-lrs", "--with-mqtt",
         "--public-url", "https://ltms.example.org", "--out", str(out)]
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


def test_web_requires_public_url(tmp_path):
    out = tmp_path / ".env"
    rc = setup_env.main(["--mode", "web", "--out", str(out)])
    assert rc != 0
    assert not out.exists()


def test_web_public_url_must_be_https(tmp_path):
    out = tmp_path / ".env"
    rc = setup_env.main(["--mode", "web", "--public-url", "http://x", "--out", str(out)])
    assert rc != 0
    assert not out.exists()


def test_web_public_url_strips_trailing_slash(tmp_path):
    out = tmp_path / ".env"
    rc = setup_env.main(
        ["--mode", "web", "--public-url", "https://ltms.example.org/", "--out", str(out)]
    )
    assert rc == 0, out.read_text() if out.exists() else "no file"
    values = _parse_env(out)
    assert values["LTMS_PUBLIC_BASE_URL"] == "https://ltms.example.org"


def test_local_public_url_defaults_to_hostname_port(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_env.socket, "gethostname", lambda: "Lab-Mac")
    out = tmp_path / ".env"
    rc = setup_env.main(["--mode", "local", "--port", "9000", "--out", str(out)])
    assert rc == 0
    values = _parse_env(out)
    assert values["LTMS_PUBLIC_BASE_URL"] == "http://lab-mac.local:9000"


def test_lrs_public_url_sets_allowed_origins(tmp_path):
    out = tmp_path / ".env"
    rc = setup_env.main(["--mode", "web", "--public-url", "https://ltms.example.org",
                         "--with-lrs", "--lrs-public-url", "https://lrs.example.org/",
                         "--out", str(out)])
    assert rc == 0
    assert _parse_env(out)["LRSQL_ALLOWED_ORIGINS"] == "https://lrs.example.org"


def test_lrs_public_url_needs_with_lrs_and_https(tmp_path):
    out = tmp_path / ".env"
    assert setup_env.main(["--mode", "web", "--public-url", "https://ltms.example.org",
                           "--lrs-public-url", "https://lrs.example.org", "--out", str(out)]) == 1
    assert setup_env.main(["--mode", "web", "--public-url", "https://ltms.example.org", "--with-lrs",
                           "--lrs-public-url", "http://lrs.example.org", "--out", str(out)]) == 1
    assert not out.exists()


def test_without_lrs_public_url_no_allowed_origins(tmp_path):
    out = tmp_path / ".env"
    assert setup_env.main(["--mode", "web", "--public-url", "https://ltms.example.org",
                           "--with-lrs", "--out", str(out)]) == 0
    assert "LRSQL_ALLOWED_ORIGINS" not in _parse_env(out)


def test_embedded_template_matches_repo_file():
    """The embedded template must equal deploy/compose/.env.example byte for
    byte, so the two can't drift."""
    repo_text = setup_env.EXAMPLE.read_text(encoding="utf-8")
    assert setup_env._EMBEDDED_TEMPLATE == repo_text


def test_runs_without_repo_template_writes_same_keys(tmp_path, monkeypatch, capsys):
    """With the repo template hidden (monkeypatched path), the script still runs
    and writes the same keys as a run with the repo template."""
    monkeypatch.setattr(setup_env, "EXAMPLE", tmp_path / "does-not-exist" / ".env.example")
    out = tmp_path / ".env"
    rc = setup_env.main(["--mode", "local", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    captured = capsys.readouterr()
    assert "embedded copy" in captured.out

    out_repo = tmp_path / "repo.env"
    assert setup_env.main(["--mode", "local", "--out", str(out_repo)]) == 0
    assert set(_parse_env(out)) == set(_parse_env(out_repo))


def test_output_has_no_cr(tmp_path):
    """Output is LF only, with no carriage returns."""
    out = tmp_path / ".env"
    setup_env.main(["--mode", "web", "--with-lrs", "--with-mqtt",
                    "--public-url", "https://ltms.example.org", "--out", str(out)])
    assert b"\r" not in out.read_bytes()


def _make_git_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    # Commit something so the repo is a real work tree (not strictly required).
    return repo


def test_git_warns_when_not_ignored(tmp_path, capsys):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = _make_git_repo(tmp_path)
    out = repo / ".env"
    setup_env.main(["--mode", "local", "--out", str(out)])
    captured = capsys.readouterr()
    assert "this file holds secrets and is not git-ignored" in captured.err


def test_git_no_warn_when_ignored(tmp_path, capsys):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = _make_git_repo(tmp_path)
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    out = repo / ".env"
    setup_env.main(["--mode", "local", "--out", str(out)])
    captured = capsys.readouterr()
    assert "this file holds secrets and is not git-ignored" not in captured.err
