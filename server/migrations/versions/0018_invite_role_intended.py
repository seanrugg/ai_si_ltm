"""Invites carry a role; enrollment can state an intended swarm and role (issue #295).

Additive only: ``invites.role`` (member|observer, default member) and the
nullable ``members.intended`` JSON column.

Revision ID: 0018
Revises: 0017
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.models.base import JSONType

# revision identifiers, used by Alembic.
revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invites", sa.Column("role", sa.String(16), nullable=False, server_default="member"))
    op.add_column("members", sa.Column("intended", JSONType, nullable=True))


def downgrade() -> None:
    op.drop_column("members", "intended")
    op.drop_column("invites", "role")
