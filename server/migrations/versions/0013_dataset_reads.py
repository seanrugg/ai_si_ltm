"""Dataset reads and full-text search (issue #238).

Adds ``dataset_reads``, and on PostgreSQL a GIN full-text index over the
extracted text of dataset items. No change to ``records`` or its triggers.

Revision ID: 0013
Revises: 0012
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

FTS_INDEX = "idx_dataset_items_fts"


def upgrade() -> None:
    op.create_table(
        "dataset_reads",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=False),
        sa.Column("cycle", sa.Uuid()),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("ts", UTCDateTime, nullable=False),
        sa.CheckConstraint("source IN ('served', 'reported')", name="ck_dataset_reads_source"),
    )
    op.create_index("idx_dataset_reads_member_ts", "dataset_reads", ["member_id", "ts"])
    op.create_index("idx_dataset_reads_dataset", "dataset_reads", ["dataset_id"])
    if op.get_bind().dialect.name == "postgresql":
        # The same expression server/services/datasets.py searches with, so the index is used.
        op.execute(f"CREATE INDEX {FTS_INDEX} ON dataset_items "
                   "USING gin (to_tsvector('english', coalesce(key, '') || ' ' || coalesce(text, '')))")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS {FTS_INDEX}")
    op.drop_index("idx_dataset_reads_dataset", table_name="dataset_reads")
    op.drop_index("idx_dataset_reads_member_ts", table_name="dataset_reads")
    op.drop_table("dataset_reads")
