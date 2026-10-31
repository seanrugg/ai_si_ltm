"""Server configuration: ``config/default.yaml`` overlaid by ``LTMS_*`` env vars."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import quote

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ltms_contract import RECORD_TYPES

DEFAULT_YAML = Path(__file__).resolve().parents[1] / "config" / "default.yaml"


class PayloadRule(BaseModel):
    """Purge the payloads of matching records once they are older than ``days``.

    ``type`` and ``tag`` are both optional; when both are given a record must
    match both. Headers are never purged, and records tagged ``keep`` are
    never touched by any rule.
    """
    type: Optional[str] = None
    tag: Optional[str] = Field(default=None, min_length=1, max_length=64)
    days: int = Field(ge=1)

    @model_validator(mode="after")
    def _check(self) -> "PayloadRule":
        if self.type is None and self.tag is None:
            raise ValueError("a retention rule needs a type, a tag, or both")
        if self.type is not None and self.type not in RECORD_TYPES:
            raise ValueError(f"unknown record type {self.type!r}")
        if self.type in ("policy", "system"):
            raise ValueError(f"{self.type} payloads are never purged")
        if self.tag == "keep":
            raise ValueError("'keep' marks records that are never purged; it can't be a rule")
        return self


DEFAULT_PAYLOAD_RULES = [
    {"type": "event", "days": 90},
    {"type": "perception", "days": 90},
    {"tag": "telemetry", "days": 30},
]


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
    retention_payload_rules: list[PayloadRule] = Field(
        default_factory=lambda: [PayloadRule(**r) for r in DEFAULT_PAYLOAD_RULES])
    retention_audit_days: int = 365

    # Scheduler (disabled in tests)
    enable_scheduler: bool = True

    # Optional: serve the built dashboard (dashboard/dist) from this process.
    # Production uses nginx for static files; this is for local/LAN use.
    dashboard_dir: str = ""

    # Secret used to encrypt LRS credentials at rest. Without it, endpoints
    # can still be created, but none may store a secret.
    secret_key: str | None = None

    # LRS bootstrap (issue #86): register an LRS endpoint from env on first
    # start. Empty LTMS_LRS_BOOTSTRAP_URL disables bootstrapping entirely.
    lrs_bootstrap_url: str = ""
    lrs_bootstrap_name: str = "default-lrs"
    lrs_bootstrap_username: str = ""
    lrs_bootstrap_secret: str = ""
    lrs_bootstrap_xapi_version: str = "2.0.0"
    lrs_bootstrap_types: str = ""
    lrs_bootstrap_enabled: bool = False

    # LRS forwarding (issue #61): how often the forwarder runs, how many records
    # per endpoint per run, and the public base URL put in translated statements.
    # Empty LTMS_PUBLIC_BASE_URL disables forwarding (one warning per run, nothing sent).
    lrs_forward_seconds: int = 30
    lrs_batch_size: int = 50
    lrs_max_batches_per_run: int = 20  # keep sending until caught up, up to this many batches per endpoint per run
    public_base_url: str = ""

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
