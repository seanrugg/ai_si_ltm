"""Memory wipe (issue #157).

Adds ``members.recall_floor_id`` and ``members.memory_generation``. No change
to ``records`` or its triggers.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("recall_floor_id", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("members", sa.Column("memory_generation", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("members", "memory_generation")
    op.drop_column("members", "recall_floor_id")
