"""Incremental integrity checks (issue #439): ``integrity_checkpoints`` and ``integrity_runs``.

Additive: two new tables, nothing in ``records`` changes. A checkpoint is how
far a member's chain has been verified plus the latest result; a run is one
background full re-verification with its progress. Both can be dropped at any
time; the next check simply verifies from genesis.

Revision ID: 0028
Revises: 0027
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.models.base import JSONType, UTCDateTime

# revision identifiers, used by Alembic.
revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integrity_checkpoints",
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_verified_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_verified_hash", sa.String(64), nullable=False),
        sa.Column("records", sa.BigInteger(), nullable=False),
        sa.Column("payloads_present", sa.BigInteger(), nullable=False),
        sa.Column("payloads_purged", sa.BigInteger(), nullable=False),
        sa.Column("deleted", sa.BigInteger(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("verified_at", UTCDateTime, nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("error", JSONType, nullable=True),
        sa.Column("head_seq", sa.BigInteger(), nullable=True),
        sa.Column("checked_at", UTCDateTime, nullable=False),
    )
    op.create_table(
        "integrity_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("started_by", sa.String(200), nullable=True),
        sa.Column("started_at", UTCDateTime, nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.Column("finished_at", UTCDateTime, nullable=True),
        sa.Column("members_total", sa.Integer(), nullable=False),
        sa.Column("members_done", sa.Integer(), nullable=False),
        sa.Column("records_done", sa.BigInteger(), nullable=False),
        sa.Column("broken", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("integrity_runs")
    op.drop_table("integrity_checkpoints")
