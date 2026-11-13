"""Admin ping (issue #356): ``members.ping_requested_at``, ``ping_requested_by``, ``ping_delivered_at``.

Additive only: nullable columns.

Revision ID: 0020
Revises: 0019
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("ping_requested_at", UTCDateTime, nullable=True))
    op.add_column("members", sa.Column("ping_requested_by", sa.String(128), nullable=True))
    op.add_column("members", sa.Column("ping_delivered_at", UTCDateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("members", "ping_delivered_at")
    op.drop_column("members", "ping_requested_by")
    op.drop_column("members", "ping_requested_at")
