"""Server configuration: ``config/default.yaml`` overlaid by ``LTMS_*`` env vars."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import quote

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
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
    # env_ignore_empty: Compose passes unset variables as "" (${VAR:-}); treat
    # those as unset so the defaults apply instead of failing to parse.
    model_config = SettingsConfigDict(env_prefix="LTMS_", extra="ignore", env_ignore_empty=True)

    # Database
    database_url: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "ltms"
    db_user: str = "ltms"
    db_password: str = ""

    # Schema startup mode. "migrate" (the default) requires a PostgreSQL
    # database to already be at the Alembic head and refuses to create tables;
    # "create" runs create_all like the old behaviour. SQLite always uses
    # create_all regardless of this mode. Env: LTMS_SCHEMA_MODE.
    schema_mode: str = "migrate"

    @field_validator("schema_mode")
    @classmethod
    def _check_schema_mode(cls, v: str) -> str:
        if v not in ("migrate", "create"):
            raise ValueError("schema_mode must be 'migrate' or 'create'")
        return v

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
    # Member addresses (#189): rows not seen for this many days are pruned nightly.
    address_history_days: int = 180

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

    # Remote MCP read access (issue #106): who may read records over MCP, and
    # what they may read when no token is presented.
    #   off      - read access is denied (default, closed).
    #   token    - only a presented, valid mcp read token grants read scope.
    #   open     - no token needed; the open scope (mcp_open_scope) applies.
    # Env: LTMS_MCP_READ_MODE.
    mcp_read_mode: str = "off"

    @field_validator("mcp_read_mode")
    @classmethod
    def _check_mcp_read_mode(cls, v: str) -> str:
        if v not in ("off", "token", "open"):
            raise ValueError("mcp_read_mode must be 'off', 'token' or 'open'")
        return v

    # The read scope granted when mode is "open" and no token is presented.
    # JSON, e.g. {"types":["thought","lesson"],"swarms":["alpha"]}. Defaults are
    # closed: an empty "types" list sees nothing. Env: LTMS_MCP_OPEN_SCOPE.
    mcp_open_scope: dict = {"types": [], "swarms": None}

    # When true, the environment (LTMS_MCP_READ_MODE / LTMS_MCP_OPEN_SCOPE) is
    # authoritative: the dashboard-stored value is ignored, writes return 409
    # locked, and the Access page shows the settings read-only. For operators
    # who want config-as-code. Env: LTMS_MCP_SETTINGS_LOCKED.
    mcp_settings_locked: bool = False

    # Enrollment request policy (issue #129):
    #   manual      - requests wait for an admin (default).
    #   auto_deny   - new requests are refused at once (403 enrollment_closed),
    #                 except a valid invite for an invite-mode swarm.
    #   auto_approve- free-agent requests are approved at once with recall only.
    # Env: LTMS_ENROLLMENT_POLICY.
    enrollment_policy: str = "manual"

    @field_validator("enrollment_policy")
    @classmethod
    def _check_enrollment_policy(cls, v: str) -> str:
        if v not in ("manual", "auto_deny", "auto_approve"):
            raise ValueError("enrollment_policy must be 'manual', 'auto_deny' or 'auto_approve'")
        return v

    # When true, the environment (LTMS_ENROLLMENT_POLICY) is authoritative: the
    # dashboard-stored value is ignored, writes return 409 locked. Env:
    # LTMS_ENROLLMENT_POLICY_LOCKED.
    enrollment_policy_locked: bool = False
    # LTMS_RETENTION_LOCKED: environment data retention settings win over the dashboard (#155)
    retention_locked: bool = False

    # Fleet datasets (#237): content-addressed files under <data_dir>/datasets.
    data_dir: str = "./data"
    dataset_text_max_mb: int = 25
    # Largest file an upload may be (#245); above this, use a catalog integration.
    # Env: LTMS_DATASET_UPLOAD_MAX_MB.
    dataset_upload_max_mb: int = 500
    # URL datasets (#249): hosts LTMS may snapshot from, comma-separated, exact
    # ("docs.ros.org") or "*.example.org". Empty (the default) means off.
    # Env: LTMS_DATASET_URL_ALLOW.
    dataset_url_allow: str = ""
    # Fleet charter (#255): days existing members have to read a new version
    # before their writes pause. Env: LTMS_CHARTER_GRACE_DAYS.
    charter_grace_days: int = 14

    # Optional offline city database for network location estimates (#191).
    # Unset (the default) means no network estimates. Env: LTMS_GEOIP_DB.
    geoip_db_path: str | None = Field(default=None, validation_alias=AliasChoices("geoip_db_path", "LTMS_GEOIP_DB"))

    # Pending enrollment requests older than this many days are expired by the
    # hourly scheduler job. Env: LTMS_PENDING_ENROLLMENT_TTL_DAYS.
    pending_enrollment_ttl_days: int = 14

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
