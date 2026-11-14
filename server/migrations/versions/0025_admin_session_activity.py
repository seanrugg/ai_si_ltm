"""Admin session idle timeout (issue #378): ``admin_sessions.last_active_at``.

Existing sessions are backfilled with ``issued_at``, so their idle time counts
from sign-in.

Revision ID: 0025
Revises: 0024
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admin_sessions", sa.Column("last_active_at", UTCDateTime, nullable=True))
    op.execute("UPDATE admin_sessions SET last_active_at = issued_at WHERE last_active_at IS NULL")


def downgrade() -> None:
    op.drop_column("admin_sessions", "last_active_at")
