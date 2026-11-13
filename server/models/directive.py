"""Directives (#303): a signed instruction from the fleet (or a swarm leader, #288) to
one swarm or all swarms. Members follow them by default and acknowledge them (#306).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, JSONType, UTCDateTime, utcnow

DIRECTIVE_STATUSES = ("open", "cancelled")


class Directive(Base):
    __tablename__ = "directives"
    __table_args__ = (
        CheckConstraint(f"status IN {DIRECTIVE_STATUSES}", name="ck_directives_status"),
        Index("idx_directives_issued_at", "issued_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope_swarm_id: Mapped[str | None] = mapped_column(String(64))  # null = all swarms
    text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONType)
    issued_by: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # The exact signed statement (JCS bytes) and its signature, served as they are.
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False)
    signer: Mapped[str] = mapped_column(String(16), nullable=False, default="fleet")  # fleet | leader
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_statement: Mapped[str | None] = mapped_column(Text)
    cancel_signature: Mapped[str | None] = mapped_column(String(128))
