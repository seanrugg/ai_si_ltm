"""Declared locations for members and swarms (issue #191).

Adds ``declared_locations``. No change to ``records`` or its triggers.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "declared_locations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subject_type", sa.String(8), nullable=False),
        sa.Column("subject_name", sa.String(64), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("room", sa.String(40)),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("subject_type", "subject_name", name="uq_declared_locations_subject"),
        sa.CheckConstraint("subject_type IN ('member', 'swarm')", name="ck_declared_locations_subject_type"),
    )


def downgrade() -> None:
    op.drop_table("declared_locations")
