"""Mutable, purgable payload for one chain header row."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, JSON, Text, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models.base import Base


class RecordPayload(Base):
    __tablename__ = "recordpayload"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    header_id: Mapped[int] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    place: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    media_type: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    header: Mapped["RecordHeader"] = relationship("RecordHeader")
