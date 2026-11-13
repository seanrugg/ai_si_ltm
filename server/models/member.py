"""Enrolled (or pending) mesh members."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Integer, LargeBinary, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, JSONType, UTCDateTime, utcnow

MEMBER_KINDS = ("robot", "agent", "assistant", "relay")
MEMBER_STATUSES = ("pending", "active", "revoked", "rejected")
COGNITION_LOCATIONS = ("onboard", "offboard")
# "relay": submit records written by other members of the same swarm, unchanged.
# Granted together with the swarm's hub role (see server/services/swarms.py).
PERMISSIONS = ("contribute", "recall", "recall:scoped", "policy", "relay")


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint(f"kind IN {MEMBER_KINDS}", name="ck_members_kind"),
        CheckConstraint(f"status IN {MEMBER_STATUSES}", name="ck_members_status"),
        CheckConstraint(f"cognition_admin IS NULL OR cognition_admin IN {COGNITION_LOCATIONS}",
                        name="ck_members_cognition_admin"),
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
    # Memory wipe (issue #157): this member recalls only records with id above
    # the floor (policies excepted). The floor only moves forward; the
    # generation counts wipes so a device can tell it was wiped.
    recall_floor_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    memory_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Where the member's AI runs (issue #185): a robot whose AI is off board is
    # Hybrid. Three sources, resolved admin > observed > declared
    # (server/services/cognition.py). `cognition_observed` is written by #199.
    cognition_admin: Mapped[str | None] = mapped_column(String(16))
    cognition_declared: Mapped[dict | None] = mapped_column(JSONType)
    cognition_observed: Mapped[dict | None] = mapped_column(JSONType)
    # What the device says it's for (#295): {"swarm", "role"}. A request shown to the
    # admin, never a grant.
    intended: Mapped[dict | None] = mapped_column(JSONType)
    # Proxied members (#311): set when this member's key is held by a sidecar on a
    # bridge host rather than by the device itself, e.g. "sidecar on <host>".
    proxied_by: Mapped[str | None] = mapped_column(String(128))
    # Admin ping (#356): a request for a heartbeat, picked up by the member or its hub.
    ping_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ping_requested_by: Mapped[str | None] = mapped_column(String(128))
    ping_delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Spec sheet (#332): what the device reports, and an admin's per-field overrides.
    specs_declared: Mapped[dict | None] = mapped_column(JSONType)
    specs_admin: Mapped[dict | None] = mapped_column(JSONType)
    specs_reported_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
