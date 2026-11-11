"""Catalog integrations (issue #248).

Adds ``catalog_integrations`` and ``datasets.integration_id``. Items of an
integration dataset are ``tier: external``: LTMS keeps the locator, members
fetch from the source and report their reads.

Revision ID: 0016
Revises: 0015
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_integrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("credential_env", sa.String(64)),
        sa.Column("description", sa.Text()),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.CheckConstraint("kind IN ('http', 's3', 'huggingface', 'ollama', 'openai', 'mcp')",
                           name="ck_catalog_integrations_kind"),
    )
    op.add_column("datasets", sa.Column("integration_id", sa.Uuid(),
                                        sa.ForeignKey("catalog_integrations.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "integration_id")
    op.drop_table("catalog_integrations")
