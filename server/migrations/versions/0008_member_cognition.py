"""Where a member's AI runs (issue #185).

Adds ``members.cognition_admin`` (with a CHECK), ``cognition_declared`` and
``cognition_observed``. No change to ``records`` or its triggers.

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import JSONType

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("cognition_admin", sa.String(16), nullable=True))
    op.add_column("members", sa.Column("cognition_declared", JSONType, nullable=True))
    op.add_column("members", sa.Column("cognition_observed", JSONType, nullable=True))
    op.create_check_constraint(
        "ck_members_cognition_admin", "members",
        "cognition_admin IS NULL OR cognition_admin IN ('onboard', 'offboard')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_members_cognition_admin", "members", type_="check")
    op.drop_column("members", "cognition_observed")
    op.drop_column("members", "cognition_declared")
    op.drop_column("members", "cognition_admin")
