"""One-time use codes that enroll someone directly into a swarm (issue #109).

Grow-by-adding-tables: nothing existing changes. An invite row holds the
SHA-256 of the plaintext code (shown to the admin exactly once), how many
uses it has left and when it expires.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, UTCDateTime, utcnow


class Invite(Base):
    """An enrollment code for a swarm, usable ``uses_left`` times."""

    __tablename__ = "invites"
    __table_args__ = (Index("idx_invites_swarm", "swarm_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    swarm_id: Mapped[str] = mapped_column(String(64), ForeignKey("swarms.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    uses_left: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
