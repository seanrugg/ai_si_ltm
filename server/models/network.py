"""Addresses members use, as seen by LTMS or reported (issues #189, #190).

Admin only. Never exported to an LRS.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import BigIntPK, Base, UTCDateTime, utcnow

ADDRESS_SOURCES = ("seen", "reported", "hub")


class MemberAddress(Base):
    __tablename__ = "member_addresses"
    __table_args__ = (
        UniqueConstraint("member_id", "ip", "source", name="uq_member_addresses_member_ip_source"),
        Index("idx_member_addresses_last_seen", "last_seen"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("members.id"), nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    # "seen": the address LTMS saw the request come from (#189);
    # "reported" / "hub": LAN addresses a member or its hub reported (#190).
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    interface: Mapped[str | None] = mapped_column(String(32))
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
