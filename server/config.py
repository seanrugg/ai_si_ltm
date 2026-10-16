"""Server configuration: ``config/default.yaml`` overlaid by ``LTMS_*`` env vars."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_YAML = Path(__file__).resolve().parents[1] / "config" / "default.yaml"


class ServerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LTMS_", extra="ignore")

    # Database
    database_url: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "ltms"
    db_user: str = "ltms"
    db_password: str = ""

    # Auth
    admin_username: str = "admin"
    admin_password: str = ""
    token_ttl_seconds: int = 300
    admin_session_ttl_seconds: int = 1800
    challenge_ttl_seconds: int = 60
    future_skew_seconds: int = 300
    cookie_secure: bool = True

    # Limits
    ingest_rate: float = 10
    recall_rate: float = 5
    max_batch: int = 100
    max_recall: int = 200
    enroll_rate_per_ip: int = 5
    max_pending_enrollments: int = 20

    # Retention
    retention_events_days: int = 90
    retention_audit_days: int = 365

    # Scheduler (disabled in tests)
    enable_scheduler: bool = True

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if not self.db_password:
            raise RuntimeError("LTMS_DB_PASSWORD (or LTMS_DATABASE_URL) must be set")
        return (
            f"postgresql+asyncpg://{quote(self.db_user)}:{quote(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


def load_config(**overrides: object) -> ServerConfig:
    """YAML defaults < environment < explicit overrides."""
    base: dict = {}
    if DEFAULT_YAML.exists():
        base = yaml.safe_load(DEFAULT_YAML.read_text()) or {}
    env_cfg = ServerConfig()  # reads env
    env_set = {k: getattr(env_cfg, k) for k in env_cfg.model_fields_set}
    return ServerConfig.model_validate({**base, **env_set, **overrides})
