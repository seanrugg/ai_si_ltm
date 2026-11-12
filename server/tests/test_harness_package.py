"""ltms-harness (#294): the client packages never pull in the server."""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLIENT_PACKAGES = ("ltms_contract", "cli_agent")
SERVER_ONLY = {"server", "fastapi", "starlette", "uvicorn", "sqlalchemy", "alembic", "asyncpg", "aiosqlite",
               "pydantic", "pydantic_settings", "apscheduler", "bcrypt", "cryptography", "mcp", "ltms_mcp",
               "yaml", "multipart"}
ALLOWED_THIRD_PARTY = {"nacl", "httpx", "paho"}


def _imports(path: Path) -> set[str]:
    """Top-level module names imported anywhere in the file (including inside functions)."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _client_files():
    for pkg in CLIENT_PACKAGES:
        yield from sorted((ROOT / pkg).glob("*.py"))


def test_client_packages_import_nothing_from_the_server():
    offenders = {f"{f.relative_to(ROOT)}: {name}" for f in _client_files() for name in _imports(f) & SERVER_ONLY}
    assert not offenders, f"client code imports server-only packages: {sorted(offenders)}"


def test_client_packages_use_only_the_harness_dependencies():
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    third = {name for f in _client_files() for name in _imports(f)} - stdlib - set(CLIENT_PACKAGES)
    assert third <= ALLOWED_THIRD_PARTY, f"undeclared dependencies for ltms-harness: {sorted(third - ALLOWED_THIRD_PARTY)}"


def test_harness_metadata_declares_only_client_dependencies():
    text = (ROOT / "harness" / "pyproject.toml").read_text()
    for dep in ("pynacl", "httpx", "paho-mqtt"):
        assert dep in text
    for server_dep in ("fastapi", "SQLAlchemy", "asyncpg", "alembic", "uvicorn"):
        assert server_dep not in text


@pytest.mark.skipif(importlib.util.find_spec("setuptools") is None,
                    reason="setuptools isn't installed here; build manually with: pip wheel ./harness")
def test_harness_wheel_builds_with_both_packages(tmp_path):
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-q",
                    str(ROOT / "harness"), "-w", str(tmp_path)], check=True)
    wheel = next(tmp_path.glob("ltms_harness-*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert "ltms_contract/record.py" in names and "ltms_contract/vectors.json" in names
    assert "cli_agent/hub_main.py" in names and "cli_agent/member_main.py" in names
    assert not any(n.startswith("server/") for n in names)
