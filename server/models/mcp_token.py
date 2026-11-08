"""MCP read tokens: named, scoped, optionally time-limited read access.

Only the SHA-256 hex of the token is stored; the plaintext is returned to the
admin exactly once, at creation time.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Integer, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, JSONType, UTCDateTime, utcnow


class McpToken(Base):
    """A long-lived read token for remote MCP clients."""

    __tablename__ = "mcp_tokens"
    __table_args__ = (
        UniqueConstraint("name", name="uq_mcp_tokens_name"),
        UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    owner: Mapped[str | None] = mapped_column(String(128))
    purpose: Mapped[str | None] = mapped_column(String(500))
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
