"""Overview's "Needs attention" snoozes (#380): one admin hides one item for a while.

Snoozing hides an item from that admin's Overview; it doesn't resolve it, and
other admins still see it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, UTCDateTime, utcnow


class AttentionSnooze(Base):
    __tablename__ = "attention_snoozes"
    __table_args__ = (UniqueConstraint("admin_id", "item_key", name="uq_attention_snoozes_admin_item"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    until: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
