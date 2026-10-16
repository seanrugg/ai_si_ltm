"""Enrolled (or pending) mesh members."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, LargeBinary, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, JSONType, UTCDateTime, utcnow

MEMBER_KINDS = ("robot", "agent", "assistant", "relay")
MEMBER_STATUSES = ("pending", "active", "revoked", "rejected")
PERMISSIONS = ("contribute", "recall", "recall:scoped", "policy")


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint(f"kind IN {MEMBER_KINDS}", name="ck_members_kind"),
        CheckConstraint(f"status IN {MEMBER_STATUSES}", name="ck_members_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    operator: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    # Granted permissions, e.g. ["contribute", "recall"]. "policy" lets a member
    # write policy records (the design doc reserves those for the administrator).
    permissions: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    # What the member asked for at enrollment (admin decides what to grant).
    requested_permissions: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    # Limits for "recall:scoped": {"types": [...], "agents": [...], "places": [...]}.
    scope: Mapped[dict | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    request_ip: Mapped[str | None] = mapped_column(String(64))
    enrolled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Chain head: highest acknowledged seq and its hash (genesis = 64 zeros).
    last_ack_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
