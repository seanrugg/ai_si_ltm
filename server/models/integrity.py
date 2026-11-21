"""Integrity checkpoints and full-check runs (issue #439).

``IntegrityCheckpoint`` keeps, per member, how far its chain has been verified
(``last_verified_seq`` / ``last_verified_hash`` and the running counts up to
there) plus the result of the latest check. A normal check starts from the
checkpoint instead of genesis. ``IntegrityRun`` is one full re-verification
run in the background, with its progress.

Neither table is part of the record contract: dropping them only costs one
full re-verification.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, JSONType, UTCDateTime, utcnow


class IntegrityCheckpoint(Base):
    __tablename__ = "integrity_checkpoints"

    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("members.id", ondelete="CASCADE"),
                                                 primary_key=True)
    # How far the chain is verified. 0 and the genesis hash mean "from the start".
    last_verified_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_verified_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Counts through last_verified_seq.
    records: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    payloads_present: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    payloads_purged: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    deleted: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # SHA-256 of the member's public key when verified; a different key voids the checkpoint.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # The latest check's result.
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    mode: Mapped[str] = mapped_column(String(12), nullable=False)
    error: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    head_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class IntegrityRun(Base):
    __tablename__ = "integrity_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(12), nullable=False, default="full")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="running")  # running|done|failed
    started_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    members_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    members_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_done: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    broken: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
