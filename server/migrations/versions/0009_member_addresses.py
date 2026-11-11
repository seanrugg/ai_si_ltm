"""Member addresses: what LTMS sees, and what members and hubs report (issue #189).

Adds ``member_addresses``. No change to ``records`` or its triggers.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "member_addresses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("interface", sa.String(32)),
        sa.Column("first_seen", UTCDateTime, nullable=False),
        sa.Column("last_seen", UTCDateTime, nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("member_id", "ip", "source", name="uq_member_addresses_member_ip_source"),
    )
    op.create_index("idx_member_addresses_last_seen", "member_addresses", ["last_seen"])


def downgrade() -> None:
    op.drop_index("idx_member_addresses_last_seen", table_name="member_addresses")
    op.drop_table("member_addresses")
