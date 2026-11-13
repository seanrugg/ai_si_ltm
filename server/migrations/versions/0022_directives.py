"""Directives (issue #303): ``directives`` table.

Revision ID: 0022
Revises: 0021
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.models.base import JSONType, UTCDateTime

# revision identifiers, used by Alembic.
revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "directives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope_swarm_id", sa.String(64)),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("payload", JSONType),
        sa.Column("issued_by", sa.String(128), nullable=False),
        sa.Column("issued_at", UTCDateTime, nullable=False),
        sa.Column("expires_at", UTCDateTime, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("signature", sa.String(128), nullable=False),
        sa.Column("signer", sa.String(16), nullable=False),
        sa.Column("cancelled_at", UTCDateTime),
        sa.Column("cancel_statement", sa.Text()),
        sa.Column("cancel_signature", sa.String(128)),
        sa.CheckConstraint("status IN ('open', 'cancelled')", name="ck_directives_status"),
    )
    op.create_index("idx_directives_issued_at", "directives", ["issued_at"])


def downgrade() -> None:
    op.drop_index("idx_directives_issued_at", table_name="directives")
    op.drop_table("directives")
