"""Swarms, swarm membership and record provenance (issue #15).

Grow-by-adding-tables: none of this changes ``records`` or the signed contract.
Swarm membership is a fact the **server** records (an admin decides it); no
record carries a swarm ID, and a member can't claim one.

* ``swarms``: a group of members with a home hub. ``id`` is the SHA-256 of the
  swarm's root public key, so it is the same on every server. The root private
  key stays offline with the operator; it is only used to appoint the hub.
* ``swarm_members``: who belongs where, with what role. History is kept: leaving
  or moving closes the row (``left_at``) and opens a new one. At most one open
  row per member (a member is in one swarm, or none = free agent) and at most
  one open ``hub`` per swarm.
* ``record_provenance``: written on ingest for every record. Which swarm the
  author was in at that moment, and which relay delivered it (null = direct).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, BigIntPK, JSONType, UTCDateTime, utcnow

SWARM_STATUSES = ("active", "revoked")
SWARM_ROLES = ("hub", "member", "observer")


class Swarm(Base):
    __tablename__ = "swarms"
    __table_args__ = (CheckConstraint(f"status IN {SWARM_STATUSES}", name="ck_swarms_status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(root public key), hex
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SwarmMember(Base):
    __tablename__ = "swarm_members"
    __table_args__ = (
        CheckConstraint(f"role IN {SWARM_ROLES}", name="ck_swarm_members_role"),
        # One open membership per member.
        Index("uq_swarm_members_open", "member_id", unique=True,
              sqlite_where=text("left_at IS NULL"), postgresql_where=text("left_at IS NULL")),
        # One open hub per swarm.
        Index("uq_swarm_members_hub", "swarm_id", unique=True,
              sqlite_where=text("role = 'hub' AND left_at IS NULL"),
              postgresql_where=text("role = 'hub' AND left_at IS NULL")),
        Index("idx_swarm_members_swarm", "swarm_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    swarm_id: Mapped[str] = mapped_column(String(64), ForeignKey("swarms.id"), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("members.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # For a hub: the appointment signed with the swarm's root key (kept as evidence).
    appointment: Mapped[dict | None] = mapped_column(JSONType)


class RecordProvenance(Base):
    __tablename__ = "record_provenance"
    __table_args__ = (
        Index("idx_record_provenance_swarm", "swarm_id"),
        Index("idx_record_provenance_relay", "relay_member_id"),
    )

    record_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), ForeignKey("records.id"), primary_key=True
    )
    swarm_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("swarms.id"))
    relay_member_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("members.id"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
