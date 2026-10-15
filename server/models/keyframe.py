"""Content-addressed image blob store (Phase 8)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, LargeBinary
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models.base import Base


class Keyframe(Base):
    __tablename__ = "keyframe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recordpayload.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_at: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False, default="image/png")

    payload: Mapped["RecordPayload"] = relationship("RecordPayload")
