"""Enrollment requests that name a swarm (issue #45).

Grow-by-adding-tables: ``members`` is unchanged. A pending enrollment request
may name the swarm it wants to join; the row is kept until approval or
rejection, then deleted.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, UTCDateTime, utcnow


class EnrollmentSwarmRequest(Base):
    """The swarm a pending enrollment asked to join. One row per member."""

    __tablename__ = "enrollment_swarm_requests"

    member_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("members.id"), primary_key=True
    )
    swarm_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("swarms.id"), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
