"""Per-swarm enrollment modes (issue #109).

Adds ``enrollment_mode`` and ``open_permissions`` to ``swarms`` and the
``invites`` table for one-time enrollment codes. No change to ``records``
or any other existing table.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa

from server.models.base import JSONType, UTCDateTime

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "swarms",
        sa.Column("enrollment_mode", sa.String(16), nullable=False,
                  server_default="approval"),
    )
    op.add_column(
        "swarms",
        sa.Column("open_permissions", JSONType, nullable=False,
                  server_default='["contribute"]'),
    )
    op.create_check_constraint(
        "ck_swarms_enrollment_mode", "swarms",
        "enrollment_mode IN ('approval', 'invite', 'open')",
    )
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("swarm_id", sa.String(64), sa.ForeignKey("swarms.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("uses_left", sa.Integer(), nullable=False),
        sa.Column("expires_at", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("code_hash", name="uq_invites_code_hash"),
    )
    op.create_index("idx_invites_swarm", "invites", ["swarm_id"])


def downgrade() -> None:
    op.drop_index("idx_invites_swarm", table_name="invites")
    op.drop_table("invites")
    op.drop_constraint("ck_swarms_enrollment_mode", "swarms", type_="check")
    op.drop_column("swarms", "open_permissions")
    op.drop_column("swarms", "enrollment_mode")
