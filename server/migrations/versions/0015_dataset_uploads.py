"""Resumable dataset uploads (issue #245).

Adds ``dataset_uploads``: one row per upload in progress. Finished uploads
become ``dataset_items`` rows and are deleted from here.

Revision ID: 0015
Revises: 0014
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_uploads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.BigInteger(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dataset_uploads")
