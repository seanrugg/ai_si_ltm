"""Declarative base and portable column types (PostgreSQL in prod, SQLite in tests)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, Integer, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# JSONB on Postgres (indexable), plain JSON elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")
# SQLite only auto-increments INTEGER PRIMARY KEY.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class UTCDateTime(TypeDecorator):
    """timestamptz on Postgres; on SQLite (no tz support) values come back naive
    and are re-tagged as UTC so the app always sees aware datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if value is not None and dialect.name == "sqlite":
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass
