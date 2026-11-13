"""Swarm leader (issues #308, #310): ``swarms.leader_member_id``, ``swarms.leader_appointment``,
``directives.inherited``, ``directives.confirmed_by``.

Revision ID: 0023
Revises: 0022
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.models.base import JSONType

# revision identifiers, used by Alembic.
revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("swarms", sa.Column("leader_member_id", sa.Uuid(), sa.ForeignKey("members.id"), nullable=True))
    op.add_column("swarms", sa.Column("leader_appointment", JSONType, nullable=True))
    op.add_column("directives", sa.Column("inherited", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("directives", sa.Column("confirmed_by", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("directives", "confirmed_by")
    op.drop_column("directives", "inherited")
    op.drop_column("swarms", "leader_appointment")
    op.drop_column("swarms", "leader_member_id")
