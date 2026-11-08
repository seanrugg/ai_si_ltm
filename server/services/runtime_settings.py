"""Runtime settings: database-backed overrides for environment configuration.

The ``mcp_access`` key holds ``{"mode": ..., "open_scope": {...}}`` and takes
precedence over ``LTMS_MCP_READ_MODE`` / ``LTMS_MCP_OPEN_SCOPE`` when present.
The environment stays the default for fresh installs and when the row is
absent.

``resolve_mcp_access`` reads the effective settings on every call (one indexed
lookup; no cache), so a change takes effect on the next MCP request without a
restart. When ``LTMS_MCP_SETTINGS_LOCKED`` is true the environment is
authoritative: the DB value is ignored and writes return 409 ``locked``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import ServerConfig
from server.models import RuntimeSetting

MCP_ACCESS_KEY = "mcp_access"

# Defaults mirror the config defaults so a fresh install behaves identically
# whether or not a row exists.
DEFAULT_MODE = "off"
DEFAULT_OPEN_SCOPE = {"types": [], "swarms": None}


def _default_mcp_access() -> dict[str, Any]:
    return {"mode": DEFAULT_MODE, "open_scope": dict(DEFAULT_OPEN_SCOPE)}


async def get_setting(session: AsyncSession, key: str) -> RuntimeSetting | None:
    """Return the row for ``key``, or None."""
    return await session.scalar(select(RuntimeSetting).where(RuntimeSetting.key == key))


async def set_setting(
    session: AsyncSession, key: str, value: dict, *, updated_by: str | None
) -> RuntimeSetting:
    """Insert or update ``key`` with ``value`` and return the row."""
    row = await get_setting(session, key)
    if row is None:
        row = RuntimeSetting(key=key, value=value, updated_by=updated_by)
        session.add(row)
    else:
        row.value = value
        row.updated_by = updated_by
    await session.flush()
    return row


async def delete_setting(session: AsyncSession, key: str) -> None:
    """Delete the row for ``key`` if it exists."""
    row = await get_setting(session, key)
    if row is not None:
        await session.delete(row)
        await session.flush()


def _env_mcp_access(cfg: ServerConfig) -> dict[str, Any]:
    """The environment-provided MCP access settings (the default source)."""
    return {
        "mode": cfg.mcp_read_mode,
        "open_scope": dict(cfg.mcp_open_scope),
    }


async def resolve_mcp_access(
    session: AsyncSession, cfg: ServerConfig
) -> dict[str, Any]:
    """The effective MCP access settings for this request.

    Returns a dict with ``mode``, ``open_scope`` and ``source``:
    ``"dashboard"`` when a DB row for ``mcp_access`` exists and the server is
    not locked, otherwise ``"environment"``. The DB row wins when present and
    unlocked; the environment is the default and is authoritative when locked.
    Reads the DB on every call (no cache).
    """
    if await is_locked(cfg):
        return {**_env_mcp_access(cfg), "source": "environment"}
    row = await get_setting(session, MCP_ACCESS_KEY)
    if row is not None:
        value = dict(row.value or {})
        return {
            "mode": value.get("mode", _env_mcp_access(cfg)["mode"]),
            "open_scope": value.get("open_scope", _env_mcp_access(cfg)["open_scope"]),
            "source": "dashboard",
        }
    return {**_env_mcp_access(cfg), "source": "environment"}


async def is_locked(cfg: ServerConfig) -> bool:
    """True when ``LTMS_MCP_SETTINGS_LOCKED`` makes the environment authoritative."""
    return bool(cfg.mcp_settings_locked)
