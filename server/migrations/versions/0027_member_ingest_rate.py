"""Per-member ingest rate limit override (issue #438 part 2).

Additive: a nullable ``members.ingest_rate`` (records per second). ``None`` means
the member uses the global config (`LTMS_INGEST_RATE`, default 10 = 600
records/min). Only writes (contribute) are affected; recall is unchanged.

Revision ID: 0027
Revises: 0026
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("ingest_rate", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "ingest_rate")
