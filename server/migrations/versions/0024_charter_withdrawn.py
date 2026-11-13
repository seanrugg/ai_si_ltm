"""Withdrawn charter versions (issue #345): ``dataset_versions.status`` may be ``withdrawn``.

Revision ID: 0024
Revises: 0023
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_dataset_versions_status", "dataset_versions", type_="check")
    op.create_check_constraint("ck_dataset_versions_status", "dataset_versions",
                               "status IN ('pending', 'approved', 'withdrawn')")


def downgrade() -> None:
    op.execute("UPDATE dataset_versions SET status = 'approved' WHERE status = 'withdrawn'")
    op.drop_constraint("ck_dataset_versions_status", "dataset_versions", type_="check")
    op.create_check_constraint("ck_dataset_versions_status", "dataset_versions", "status IN ('pending', 'approved')")
