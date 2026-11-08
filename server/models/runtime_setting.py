"""Runtime settings: keyed configuration stored in the database.

The ``mcp_access`` key holds {"mode": ..., "open_scope": {...}} and takes
precedence over the LTMS_MCP_READ_MODE / LTMS_MCP_OPEN_SCOPE environment
variables when present. The environment stays the default for fresh installs.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, JSONType, UTCDateTime, utcnow


class RuntimeSetting(Base):
    """A single keyed setting whose JSON value overrides the environment."""

    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(64))
