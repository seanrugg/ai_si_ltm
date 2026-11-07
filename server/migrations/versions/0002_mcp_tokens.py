"""MCP read tokens (issue #106).

Adds the ``mcp_tokens`` table for named, scoped, optionally time-limited read
tokens used by remote MCP clients. No change to ``records`` or any other
existing table.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa

from server.models.base import JSONType, UTCDateTime

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True, default=uuid.uuid4),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scope", JSONType, nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("expires_at", UTCDateTime),
        sa.Column("revoked_at", UTCDateTime),
        sa.Column("last_used_at", UTCDateTime),
        sa.UniqueConstraint("name", name="uq_mcp_tokens_name"),
        sa.UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("mcp_tokens")
