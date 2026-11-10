"""Record tombstones: an admin can delete a single record (issue #173).

Adds ``record_tombstones`` (append-only, enforced by triggers) and replaces
the body of ``records_no_mutation()`` so a ``records`` row can be deleted only
when its tombstone (same member, seq and hash) already exists. UPDATE and
TRUNCATE on ``records`` stay rejected. The existing ``records`` triggers call
the function by name, so they are not dropped; replacing the function is the
whole change to them (AGENTS.md §7: approved by Sean, 9 Nov 2026).

PostgreSQL only, like every revision since 0001.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from server.models.base import UTCDateTime

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_RECORDS_FUNC_NEW = """
CREATE OR REPLACE FUNCTION records_no_mutation() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF EXISTS (SELECT 1 FROM record_tombstones t
               WHERE t.member_id = OLD.member_id AND t.seq = OLD.seq AND t.hash = OLD.hash) THEN
      RETURN OLD;
    END IF;
  END IF;
  RAISE EXCEPTION 'records table is append-only: % is not permitted', TG_OP;
END
$$ LANGUAGE plpgsql
"""

_RECORDS_FUNC_OLD = """
CREATE OR REPLACE FUNCTION records_no_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'records table is append-only: % is not permitted', TG_OP;
END
$$ LANGUAGE plpgsql
"""

_TOMB_FUNC = """
CREATE OR REPLACE FUNCTION record_tombstones_no_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'record_tombstones table is append-only: % is not permitted', TG_OP;
END
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.create_table(
        "record_tombstones",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("deleted_at", UTCDateTime, nullable=False),
        sa.Column("deleted_by", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hash"),
        sa.UniqueConstraint("member_id", "seq", name="uq_record_tombstones_member_seq"),
    )
    op.execute(sa.text(_RECORDS_FUNC_NEW))
    op.execute(sa.text(_TOMB_FUNC))
    op.execute(sa.text(
        "CREATE TRIGGER trg_record_tombstones_no_mutation BEFORE UPDATE OR DELETE ON record_tombstones "
        "FOR EACH ROW EXECUTE FUNCTION record_tombstones_no_mutation()"))
    op.execute(sa.text(
        "CREATE TRIGGER trg_record_tombstones_no_truncate BEFORE TRUNCATE ON record_tombstones "
        "FOR EACH STATEMENT EXECUTE FUNCTION record_tombstones_no_mutation()"))


def downgrade() -> None:
    # Restore the original rule first, so no record can be deleted once
    # the tombstones that allowed it are gone.
    op.execute(sa.text(_RECORDS_FUNC_OLD))
    op.execute(sa.text("DROP TRIGGER trg_record_tombstones_no_truncate ON record_tombstones"))
    op.execute(sa.text("DROP TRIGGER trg_record_tombstones_no_mutation ON record_tombstones"))
    op.execute(sa.text("DROP FUNCTION record_tombstones_no_mutation()"))
    op.drop_table("record_tombstones")
