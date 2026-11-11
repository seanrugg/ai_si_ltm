"""Fleet datasets: catalog, versions and items (issue #236, docs/datasets-design.md).

Adds ``datasets``, ``dataset_versions`` and ``dataset_items``. No change to
``records`` or its triggers.

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import JSONType, UTCDateTime

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="fleet"),
        sa.Column("licence", sa.Text(), nullable=False),
        sa.Column("use", sa.String(16), nullable=False, server_default="reference"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("added_by", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64)),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.CheckConstraint("scope IN ('fleet')", name="ck_datasets_scope"),
        sa.CheckConstraint("kind IN ('upload', 'url', 'integration')", name="ck_datasets_kind"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'retired')", name="ck_datasets_status"),
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dataset_id", sa.Uuid(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("manifest_hash", sa.String(64)),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("snapshot_at", UTCDateTime),
        sa.Column("approved_by", sa.String(64)),
        sa.Column("approved_at", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_version"),
        sa.CheckConstraint("status IN ('pending', 'approved')", name="ck_dataset_versions_status"),
    )
    op.create_table(
        "dataset_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("version_id", sa.BigInteger(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("meta", JSONType),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("version_id", "key", name="uq_dataset_items_key"),
        sa.CheckConstraint("tier IN ('text', 'large', 'external')", name="ck_dataset_items_tier"),
    )
    op.create_index("idx_dataset_items_sha256", "dataset_items", ["sha256"])


def downgrade() -> None:
    op.drop_index("idx_dataset_items_sha256", table_name="dataset_items")
    op.drop_table("dataset_items")
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
