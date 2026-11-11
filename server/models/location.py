"""Locations an admin declares for a member or a swarm (issue #191).

Admin only. Never sent to an LRS.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, UTCDateTime, utcnow

LOCATION_SUBJECTS = ("member", "swarm")


class DeclaredLocation(Base):
    __tablename__ = "declared_locations"
    __table_args__ = (
        UniqueConstraint("subject_type", "subject_name", name="uq_declared_locations_subject"),
        CheckConstraint(f"subject_type IN {LOCATION_SUBJECTS}", name="ck_declared_locations_subject_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(8), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    room: Mapped[str | None] = mapped_column(String(40))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
