"""Member spec sheet (issue #332): ``members.specs_declared``, ``specs_admin``, ``specs_reported_at``.

Additive only: nullable columns.

Revision ID: 0021
Revises: 0020
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.models.base import JSONType, UTCDateTime

# revision identifiers, used by Alembic.
revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("specs_declared", JSONType, nullable=True))
    op.add_column("members", sa.Column("specs_admin", JSONType, nullable=True))
    op.add_column("members", sa.Column("specs_reported_at", UTCDateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("members", "specs_reported_at")
    op.drop_column("members", "specs_admin")
    op.drop_column("members", "specs_declared")
