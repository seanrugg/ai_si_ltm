"""Removed swarms (issue #232).

Adds ``swarms.removed_at`` and ``swarms.former_name``, and allows status
``removed``. No change to ``records`` or its triggers.

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("swarms", sa.Column("removed_at", UTCDateTime, nullable=True))
    op.add_column("swarms", sa.Column("former_name", sa.String(64), nullable=True))
    op.drop_constraint("ck_swarms_status", "swarms", type_="check")
    op.create_check_constraint("ck_swarms_status", "swarms", "status IN ('active', 'revoked', 'removed')")


def downgrade() -> None:
    op.drop_constraint("ck_swarms_status", "swarms", type_="check")
    op.create_check_constraint("ck_swarms_status", "swarms", "status IN ('active', 'revoked')")
    op.drop_column("swarms", "former_name")
    op.drop_column("swarms", "removed_at")
