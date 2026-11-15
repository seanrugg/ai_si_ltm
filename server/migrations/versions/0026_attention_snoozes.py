"""Overview "Needs attention" snoozes (issue #380): ``attention_snoozes``.

One row per admin and item; snoozing again moves ``until``.

Revision ID: 0026
Revises: 0025
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attention_snoozes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("admin_id", sa.Uuid(), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=False),
        sa.Column("until", UTCDateTime, nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("admin_id", "item_key", name="uq_attention_snoozes_admin_item"),
    )


def downgrade() -> None:
    op.drop_table("attention_snoozes")
