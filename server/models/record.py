"""Chain headers (permanent, append-only) and their purgeable payloads."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DDL,
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, BigIntPK, JSONType, UTCDateTime, utcnow


class Record(Base):
    """One record's header. Never updated or deleted (enforced by triggers).

    ``header_raw`` holds the exact canonical bytes that were hashed and signed,
    so integrity checks never depend on how the database round-trips JSON or
    timestamps. The other columns are parsed copies for querying.
    """

    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("member_id", "seq", name="uq_records_member_seq"),
        Index("idx_records_ts", "ts"),
        Index("idx_records_type_ts", "type", "ts"),
        Index("idx_records_place", "place"),
        Index("idx_records_outcome", "outcome"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("members.id"), nullable=False)
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    place: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str | None] = mapped_column(String(16))
    peers: Mapped[list | None] = mapped_column(JSONType)
    tags: Mapped[list | None] = mapped_column(JSONType)
    refs: Mapped[dict | None] = mapped_column(JSONType)
    sw: Mapped[str | None] = mapped_column(String(128))
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    sig: Mapped[str] = mapped_column(String(128), nullable=False)
    header_raw: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class RecordPayload(Base):
    """A record's payload. Retention may delete these rows; the header stays."""

    __tablename__ = "record_payloads"

    record_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("records.id"),
        primary_key=True,
    )
    payload_raw: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    body: Mapped[dict] = mapped_column(JSONType, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)


# --- append-only enforcement ------------------------------------------------ #
_PG_FUNC = DDL(
    """
CREATE OR REPLACE FUNCTION records_no_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'records table is append-only: %% is not permitted', TG_OP;
END
$$ LANGUAGE plpgsql
"""
)
_PG_ROW_TRIGGER = DDL(
    "CREATE TRIGGER trg_records_no_mutation BEFORE UPDATE OR DELETE ON records "
    "FOR EACH ROW EXECUTE FUNCTION records_no_mutation()"
)
_PG_TRUNCATE_TRIGGER = DDL(
    "CREATE TRIGGER trg_records_no_truncate BEFORE TRUNCATE ON records "
    "FOR EACH STATEMENT EXECUTE FUNCTION records_no_mutation()"
)
_SQLITE_NO_UPDATE = DDL(
    "CREATE TRIGGER trg_records_no_update BEFORE UPDATE ON records "
    "BEGIN SELECT RAISE(ABORT, 'records table is append-only: UPDATE is not permitted'); END"
)
_SQLITE_NO_DELETE = DDL(
    "CREATE TRIGGER trg_records_no_delete BEFORE DELETE ON records "
    "BEGIN SELECT RAISE(ABORT, 'records table is append-only: DELETE is not permitted'); END"
)

for _ddl in (_PG_FUNC, _PG_ROW_TRIGGER, _PG_TRUNCATE_TRIGGER):
    event.listen(Record.__table__, "after_create", _ddl.execute_if(dialect="postgresql"))
for _ddl in (_SQLITE_NO_UPDATE, _SQLITE_NO_DELETE):
    event.listen(Record.__table__, "after_create", _ddl.execute_if(dialect="sqlite"))

APPEND_ONLY_DDL_POSTGRES = (_PG_FUNC, _PG_ROW_TRIGGER, _PG_TRUNCATE_TRIGGER)
