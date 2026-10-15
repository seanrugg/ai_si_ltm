"""Server configuration — pydantic-settings, env-driven, YAML base.

Config is layered: base values come from ``config/default.yaml`` (nested sections
matching the plan's env-var groups), and every scalar can be overridden by an
environment variable with the ``LTMS_`` prefix. The group prefixes mirror that
document — e.g. ``LTMS_INGEST_RATE`` maps onto ``rate_limits.ingest_requests_per_second``.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSection(BaseSettings):
    """Runtime server binding / worker settings."""

    host: str = Field("0.0.0.0", description="Bind address")
    port: int = Field(8080, ge=1, le=65535, description="Bind port")
    workers: int = Field(1, ge=1, description="Uvicorn worker count (single-worker by default)")


class AuthSection(BaseSettings):
    """Admin login + replay-window settings."""

    token_ttl_seconds: int = Field(300, ge=0, description="Member token lifetime (s)")
    admin_session_ttl_seconds: int = Field(
        1800, ge=0, description="Admin httpOnly cookie lifetime (s)"
    )
    max_replay_sec: int = Field(45, ge=0, description="Auth nonce replay window (s)")
    clock_skew_sec: int = Field(3600, ge=0, description="ts may not exceed this from now() (s)")


class RateLimits(BaseSettings):
    """Per-member request budgets (in-memory limiter, see security layer)."""

    ingest_requests_per_second: int = Field(10, ge=1)
    recall_requests_per_second: int = Field(5, ge=1)
    max_batch: int = Field(100, ge=1)
    max_recall: int = Field(200, ge=1)
    enroll_per_ip: int = Field(5, ge=0, description="Per-IP capacity cap on /api/auth/enroll")
    challenge_per_ip: int = Field(10, ge=0, description="Per-IP cap on /api/auth/challenge")


class Retention(BaseSettings):
    """Retention tiers (days) + scheduler timing (local wall-clock)."""

    events_days: int = Field(90, ge=0)
    keyframes_days: int = Field(30, ge=0)
    audit_days: int = Field(365, ge=0)
    rollup_at_local: str = Field("03:00", description="Daily event rollup time (HH:MM local)")
    purge_keyframes_at_local: str = Field("04:00", description="Daily keyframe purge (HH:MM local)")
    audit_prune_weekday: int = Field(0, ge=0, le=6, description="Monday=0 ... Sunday=6")


class ServerConfig(BaseSettings):
    """Top-level config. Groups map 1:1 to the nested sections above."""

    model_config = SettingsConfigDict(env_prefix="LTMS_", env_file=".env", extra="ignore")

    # --- server -----------------------------------------------------------
    server: ServerSection = ServerSection()

    # --- auth (root scalars kept for one-line access) ---------------------
    admin_username: str = Field("admin", description="Admin username")
    admin_password: str = Field("", description="Admin password (env-provided)")

    # --- database ---------------------------------------------------------
    database_url: str = Field(
        "sqlite+aiosqlite:///./ltms.db",
        description="Async DB URL (sqlite for local/test, postgresql for prod)",
    )

    # --- crypto contract --------------------------------------------------
    body_hash_required: bool = True

    auth: AuthSection = AuthSection()
    rate_limits: RateLimits = RateLimits()
    retention: Retention = Retention()


@lru_cache
def get_config() -> ServerConfig:
    """Return a cached, fully-resolved config object."""
    base = {}
    try:
        import yaml

        default_yaml = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
        if default_yaml.exists():
            base = yaml.safe_load(default_yaml.read_text()) or {}
    except Exception:
        base = {}
    return ServerConfig(**base)
