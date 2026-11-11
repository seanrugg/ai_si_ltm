"""LRS endpoints forward dataset reads (issue #239).

Adds ``send_reads`` (default true) and ``last_read_id`` (the reads cursor) to
``lrs_endpoints``. Existing endpoints start from the newest read (live-only).

Revision ID: 0014
Revises: 0013
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lrs_endpoints", sa.Column("send_reads", sa.Boolean(), nullable=False,
                                             server_default=sa.true()))
    op.add_column("lrs_endpoints", sa.Column("last_read_id", sa.BigInteger(), nullable=False,
                                             server_default="0"))
    op.execute("UPDATE lrs_endpoints SET last_read_id = "
               "(SELECT coalesce(max(id), 0) FROM dataset_reads)")


def downgrade() -> None:
    op.drop_column("lrs_endpoints", "last_read_id")
    op.drop_column("lrs_endpoints", "send_reads")
