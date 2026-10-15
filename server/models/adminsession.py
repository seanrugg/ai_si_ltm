"""Admin session cookie store (separate from member tokens)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Text, ForeignKey, LargeBinary, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    session_id: Mapped[bytes] = mapped_column(
        LargeBinary, primary_key=True, nullable=False
    )
    username: Mapped[str] = mapped_column(
        Text, ForeignKey("admin_user.username"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
