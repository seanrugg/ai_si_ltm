"""Empty environment variables fall back to defaults.

Compose passes unset variables through as empty strings (``${VAR:-}``), so a
stack without the LRS or MCP settings must still start.
"""
from __future__ import annotations

from server.config import ServerConfig


def test_empty_env_vars_use_defaults(monkeypatch):
    for var in ("LTMS_LRS_BOOTSTRAP_ENABLED", "LTMS_LRS_BOOTSTRAP_NAME",
                "LTMS_LRS_BOOTSTRAP_XAPI_VERSION", "LTMS_MCP_READ_MODE",
                "LTMS_MCP_OPEN_SCOPE", "LTMS_PUBLIC_BASE_URL"):
        monkeypatch.setenv(var, "")
    cfg = ServerConfig()
    assert cfg.lrs_bootstrap_enabled is False
    assert cfg.lrs_bootstrap_name == "default-lrs"
    assert cfg.lrs_bootstrap_xapi_version == "2.0.0"
    assert cfg.mcp_read_mode == "off"
    assert cfg.mcp_open_scope == {"types": [], "swarms": None}


def test_mcp_settings_from_env(monkeypatch):
    monkeypatch.setenv("LTMS_MCP_READ_MODE", "open")
    monkeypatch.setenv("LTMS_MCP_OPEN_SCOPE", '{"types":["lesson"],"swarms":null}')
    cfg = ServerConfig()
    assert cfg.mcp_read_mode == "open"
    assert cfg.mcp_open_scope == {"types": ["lesson"], "swarms": None}
