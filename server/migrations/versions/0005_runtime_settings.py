"""Runtime settings (issue #120).

Adds the ``runtime_settings`` table (key PK, value JSON, updated_at,
updated_by) so MCP read mode and open scope can be managed from the
dashboard without editing .env and redeploying. No change to ``records`` or
any other table.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import JSONType, UTCDateTime

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_settings",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", JSONType, nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_runtime_settings"),
    )


def downgrade() -> None:
    op.drop_table("runtime_settings")
