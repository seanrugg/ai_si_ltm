"""Token metadata (issue #117).

Adds ``owner``, ``purpose`` and ``use_count`` to ``mcp_tokens``. No change to
``records`` or any other table.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_tokens",
        sa.Column("owner", sa.String(128), nullable=True),
    )
    op.add_column(
        "mcp_tokens",
        sa.Column("purpose", sa.String(500), nullable=True),
    )
    op.add_column(
        "mcp_tokens",
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("mcp_tokens", "use_count")
    op.drop_column("mcp_tokens", "purpose")
    op.drop_column("mcp_tokens", "owner")
