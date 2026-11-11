"""Fleet datasets: reference material members read (docs/datasets-design.md).

Every dataset belongs to the fleet (``scope = "fleet"``) and is readable by
any member once an admin approves it. Members read; LTMS never trains on it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import BigIntPK, Base, JSONType, UTCDateTime, utcnow

DATASET_SCOPES = ("fleet",)
DATASET_KINDS = ("upload", "url", "integration")
DATASET_STATUSES = ("pending", "approved", "retired")
VERSION_STATUSES = ("pending", "approved")
ITEM_TIERS = ("text", "large", "external")


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("scope IN ('fleet')", name="ck_datasets_scope"),
        CheckConstraint(f"kind IN {DATASET_KINDS}", name="ck_datasets_kind"),
        CheckConstraint(f"status IN {DATASET_STATUSES}", name="ck_datasets_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Who may read it. Only "fleet" for now: every member of the fleet (decided 11 Nov 2026).
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="fleet", server_default="fleet")
    licence: Mapped[str] = mapped_column(Text, nullable=False)
    use: Mapped[str] = mapped_column(String(16), nullable=False, default="reference", server_default="reference")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    added_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_version"),
        CheckConstraint(f"status IN {VERSION_STATUSES}", name="ck_dataset_versions_status"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("datasets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    manifest_hash: Mapped[str | None] = mapped_column(String(64))
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    source_url: Mapped[str | None] = mapped_column(Text)
    snapshot_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class DatasetItem(Base):
    __tablename__ = "dataset_items"
    __table_args__ = (
        UniqueConstraint("version_id", "key", name="uq_dataset_items_key"),
        CheckConstraint(f"tier IN {ITEM_TIERS}", name="ck_dataset_items_tier"),
        Index("idx_dataset_items_sha256", "sha256"),
        # The PostgreSQL full-text index (idx_dataset_items_fts) is created by migration 0013.
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), ForeignKey("dataset_versions.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    text: Mapped[str | None] = mapped_column(Text)  # extracted, at most 1 MB
    meta: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


READ_SOURCES = ("served", "reported")


class DatasetRead(Base):
    """One member reading one item (#238). Repeat reads in the same cycle aren't logged again."""
    __tablename__ = "dataset_reads"
    __table_args__ = (
        CheckConstraint(f"source IN {READ_SOURCES}", name="ck_dataset_reads_source"),
        Index("idx_dataset_reads_member_ts", "member_id", "ts"),
        Index("idx_dataset_reads_dataset", "dataset_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("members.id"), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("datasets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    cycle: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="served")
    ts: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class DatasetUpload(Base):
    """A resumable upload in progress (#245). The bytes so far are in
    ``<data_dir>/datasets/uploads/<id>.part``; unfinished ones expire after 24 h."""
    __tablename__ = "dataset_uploads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), ForeignKey("dataset_versions.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
