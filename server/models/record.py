"""Chain HEADER row (permanent, append-only)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, JSON, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base


class RecordHeader(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    place: Mapped[str | None] = mapped_column(Text, nullable=True)
    peers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    body_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sig: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
