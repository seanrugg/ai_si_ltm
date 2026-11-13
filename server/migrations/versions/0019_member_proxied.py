"""Proxied members (issue #311): ``members.proxied_by``.

Additive only: a nullable string naming who holds a proxied member's key.

Revision ID: 0019
Revises: 0018
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("proxied_by", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "proxied_by")
