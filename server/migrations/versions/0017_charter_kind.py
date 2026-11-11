"""Datasets of kind ``charter`` (issue #255): the reserved ``fleet-charter`` dataset.

Revision ID: 0017
Revises: 0016
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_datasets_kind", "datasets", type_="check")
    op.create_check_constraint("ck_datasets_kind", "datasets", "kind IN ('upload', 'url', 'integration', 'charter')")


def downgrade() -> None:
    op.drop_constraint("ck_datasets_kind", "datasets", type_="check")
    op.create_check_constraint("ck_datasets_kind", "datasets", "kind IN ('upload', 'url', 'integration')")
