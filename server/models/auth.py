"""Auth state: login challenges, member tokens, admin users and sessions."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, LargeBinary, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, JSONType, UTCDateTime, utcnow


class AuthChallenge(Base):
    """Single-use nonce handed out by /api/auth/challenge. Lives in the DB so it
    works with more than one worker."""

    __tablename__ = "auth_challenges"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("members.id"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class Token(Base):
    """Short-lived member bearer token. Only the SHA-256 of the token is stored."""

    __tablename__ = "tokens"
    __table_args__ = (Index("idx_tokens_member", "member_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("members.id"), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    permissions: Mapped[list] = mapped_column(JSONType, nullable=False)
    remote_ip: Mapped[str | None] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class AdminSession(Base):
    """Admin cookie session. Only the SHA-256 of the cookie value is stored."""

    __tablename__ = "admin_sessions"

    session_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    admin_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("admin_users.id"), nullable=False)
    remote_ip: Mapped[str | None] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
