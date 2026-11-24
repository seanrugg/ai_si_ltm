"""Ping delivery (issue #384): ``members.ping_delivered_by``.

Additive: one nullable column on ``members``; nothing in ``records`` changes.
It records who fetched a pending ping: the member itself, or the hub that passed
it on, so the dashboard can say "passed to the hub" rather than "picked up by
the member". Rows from before this revision stay NULL and read as "delivered".

Revision ID: 0029
Revises: 0028
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("ping_delivered_by", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "ping_delivered_by")
