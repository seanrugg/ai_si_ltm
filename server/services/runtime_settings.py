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
from server.models.base import utcnow

MCP_ACCESS_KEY = "mcp_access"
ENROLLMENT_POLICY_KEY = "enrollment_policy"
RETENTION_KEY = "retention"

# Defaults mirror the config defaults so a fresh install behaves identically
# whether or not a row exists.
DEFAULT_MODE = "off"
DEFAULT_OPEN_SCOPE = {"types": [], "swarms": None}
DEFAULT_ENROLLMENT_POLICY = "manual"


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
        row.updated_at = utcnow()  # the column default only applies on insert
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


def _default_enrollment_policy() -> dict[str, Any]:
    return {"policy": DEFAULT_ENROLLMENT_POLICY}


def _env_enrollment_policy(cfg: ServerConfig) -> dict[str, Any]:
    """The environment-provided enrollment policy (the default source)."""
    return {"policy": cfg.enrollment_policy}


async def resolve_enrollment_policy(
    session: AsyncSession, cfg: ServerConfig
) -> dict[str, Any]:
    """The effective enrollment policy for this request.

    Returns a dict with ``policy`` and ``source``: ``"dashboard"`` when a DB row
    for ``enrollment_policy`` exists and the server is not locked, otherwise
    ``"environment"``. The DB row wins when present and unlocked; the
    environment is the default and is authoritative when locked. Reads the DB on
    every call (no cache).
    """
    if await is_enrollment_policy_locked(cfg):
        return {**_env_enrollment_policy(cfg), "source": "environment"}
    row = await get_setting(session, ENROLLMENT_POLICY_KEY)
    if row is not None:
        value = dict(row.value or {})
        return {
            "policy": value.get("policy", _env_enrollment_policy(cfg)["policy"]),
            "source": "dashboard",
        }
    return {**_env_enrollment_policy(cfg), "source": "environment"}


async def is_enrollment_policy_locked(cfg: ServerConfig) -> bool:
    """True when ``LTMS_ENROLLMENT_POLICY_LOCKED`` makes the environment authoritative."""
    return bool(cfg.enrollment_policy_locked)


# ---- data retention (issue #155) ---------------------------------------------

def _env_retention(cfg: ServerConfig) -> dict[str, Any]:
    return {
        "payload_rules": [r.model_dump() for r in cfg.retention_payload_rules],
        "audit_days": cfg.retention_audit_days,
    }


def is_retention_locked(cfg: ServerConfig) -> bool:
    """True when ``LTMS_RETENTION_LOCKED`` makes the environment authoritative."""
    return bool(cfg.retention_locked)


async def resolve_retention(session: AsyncSession, cfg: ServerConfig) -> dict[str, Any]:
    """The effective data retention settings, read on every call (no cache).

    ``payload_rules`` is a list of ``PayloadRule`` dicts; an empty list means no
    payload ever expires. ``audit_days`` of None means the audit log is never
    pruned. The dashboard row wins unless the server is locked.
    """
    env = _env_retention(cfg)
    if is_retention_locked(cfg):
        return {**env, "source": "environment"}
    row = await get_setting(session, RETENTION_KEY)
    if row is not None:
        value = dict(row.value or {})
        return {
            "payload_rules": list(value.get("payload_rules", env["payload_rules"])),
            "audit_days": value.get("audit_days", env["audit_days"]),
            "source": "dashboard",
        }
    return {**env, "source": "environment"}


MIN_AUDIT_DAYS = 30


def validate_retention(payload_rules: list, audit_days: int | None) -> dict[str, Any]:
    """Check a data retention change; ServiceError 422 ``bad_retention`` if invalid.

    Each rule must be a valid ``PayloadRule`` (so policy/system payloads and the
    ``keep`` tag stay protected); ``audit_days`` is None (keep forever) or >= 30.
    """
    from pydantic import ValidationError

    from server.config import PayloadRule
    from server.services.errors import ServiceError

    rules = []
    for i, raw in enumerate(payload_rules):
        try:
            rules.append(PayloadRule(**raw).model_dump())
        except (ValidationError, TypeError) as exc:
            msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            raise ServiceError(422, "bad_retention", f"rule {i + 1}: {msg}") from None
    if audit_days is not None and audit_days < MIN_AUDIT_DAYS:
        raise ServiceError(422, "bad_retention",
                           f"audit_days must be null (keep forever) or at least {MIN_AUDIT_DAYS}")
    return {"payload_rules": rules, "audit_days": audit_days}


# ---- admin sessions (issue #378) ---------------------------------------------

ADMIN_SESSIONS_KEY = "admin_sessions"

IDLE_MINUTES_MIN = 15
IDLE_MINUTES_MAX = 8 * 60
MAX_HOURS_MIN = 1
MAX_HOURS_MAX = 24
WARN_MINUTES_MIN = 1
WARN_MINUTES_MAX = 10


def admin_session_limits(cfg: ServerConfig) -> dict[str, int]:
    """Allowed ranges. ``LTMS_ADMIN_IDLE_MAX_MINUTES`` can lower the idle ceiling
    (a site's session-lock policy); it never raises it past 8 h."""
    idle_max = max(IDLE_MINUTES_MIN, min(IDLE_MINUTES_MAX, int(cfg.admin_idle_max_minutes)))
    return {
        "idle_minutes_min": IDLE_MINUTES_MIN, "idle_minutes_max": idle_max,
        "max_hours_min": MAX_HOURS_MIN, "max_hours_max": MAX_HOURS_MAX,
        "warn_minutes_min": WARN_MINUTES_MIN, "warn_minutes_max": WARN_MINUTES_MAX,
    }


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _env_admin_sessions(cfg: ServerConfig) -> dict[str, int]:
    lim = admin_session_limits(cfg)
    return {
        "idle_minutes": _clamp(cfg.admin_idle_minutes, lim["idle_minutes_min"], lim["idle_minutes_max"]),
        "max_hours": _clamp(cfg.admin_session_max_hours, lim["max_hours_min"], lim["max_hours_max"]),
        "warn_minutes": _clamp(cfg.admin_warn_minutes, lim["warn_minutes_min"], lim["warn_minutes_max"]),
    }


async def resolve_admin_sessions(session: AsyncSession, cfg: ServerConfig) -> dict[str, Any]:
    """The effective admin session timeouts, read on every check (no cache), so a
    change applies to existing sessions at their next request.

    The dashboard row wins over the environment, but the idle timeout is always
    held to the environment ceiling (``LTMS_ADMIN_IDLE_MAX_MINUTES``), even if
    the ceiling was lowered after the row was saved.
    """
    env = _env_admin_sessions(cfg)
    lim = admin_session_limits(cfg)
    row = await get_setting(session, ADMIN_SESSIONS_KEY)
    if row is None:
        return {**env, "source": "environment"}
    value = dict(row.value or {})
    idle = _clamp(value.get("idle_minutes", env["idle_minutes"]), lim["idle_minutes_min"], lim["idle_minutes_max"])
    max_hours = _clamp(value.get("max_hours", env["max_hours"]), lim["max_hours_min"], lim["max_hours_max"])
    warn = _clamp(value.get("warn_minutes", env["warn_minutes"]), lim["warn_minutes_min"], lim["warn_minutes_max"])
    return {"idle_minutes": idle, "max_hours": max_hours, "warn_minutes": warn, "source": "dashboard"}


def validate_admin_sessions(cfg: ServerConfig, idle_minutes, max_hours, warn_minutes) -> dict[str, int]:
    """Check an admin session change; ServiceError 422 ``bad_admin_sessions`` if invalid."""
    from server.services.errors import ServiceError

    lim = admin_session_limits(cfg)

    def whole(name, v):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ServiceError(422, "bad_admin_sessions", f"{name} must be a whole number")
        return v

    idle = whole("idle_minutes", idle_minutes)
    max_hours = whole("max_hours", max_hours)
    warn = whole("warn_minutes", warn_minutes)
    if not lim["idle_minutes_min"] <= idle <= lim["idle_minutes_max"]:
        raise ServiceError(422, "bad_admin_sessions",
                           f"sign out after inactivity must be {lim['idle_minutes_min']}-{lim['idle_minutes_max']} minutes")
    if not lim["max_hours_min"] <= max_hours <= lim["max_hours_max"]:
        raise ServiceError(422, "bad_admin_sessions",
                           f"maximum session length must be {lim['max_hours_min']}-{lim['max_hours_max']} hours")
    if max_hours * 60 < idle:
        raise ServiceError(422, "bad_admin_sessions",
                           "maximum session length must be at least the inactivity timeout")
    if not lim["warn_minutes_min"] <= warn <= lim["warn_minutes_max"]:
        raise ServiceError(422, "bad_admin_sessions",
                           f"warning must be {lim['warn_minutes_min']}-{lim['warn_minutes_max']} minutes before sign-out")
    if warn >= idle:
        raise ServiceError(422, "bad_admin_sessions", "the warning must come before the inactivity timeout")
    return {"idle_minutes": idle, "max_hours": max_hours, "warn_minutes": warn}
