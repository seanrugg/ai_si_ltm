"""Frozen baseline of today's schema (issue #97).

Byte-for-byte copy of ``config/schema.sql`` at revision 0001. Never edited
again; future schema changes add new migrations on top of this one.
"""
from __future__ import annotations

import os
from typing import List

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def split_sql(text: str) -> List[str]:
    """Split a SQL file into individual statements.

    asyncpg can't run several statements in one call, so the file is split
    on semicolons that end a line, keeping ``$$ ... $$`` dollar-quoted
    function bodies intact.
    """
    out: List[str] = []
    buf: List[str] = []
    in_dollar = False
    for line in text.splitlines():
        if not buf and (not line.strip() or line.lstrip().startswith("--")):
            continue
        buf.append(line)
        if line.count("$$") % 2 == 1:
            in_dollar = not in_dollar
        if not in_dollar and line.rstrip().endswith(";"):
            out.append("\n".join(buf))
            buf = []
    if "".join(buf).strip():
        out.append("\n".join(buf))
    return out


def _baseline_sql_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "sql", "0001_baseline.sql")


def upgrade() -> None:
    with open(_baseline_sql_path()) as fh:
        text = fh.read()
    for stmt in split_sql(text):
        op.execute(sa.text(stmt))


def downgrade() -> None:
    raise RuntimeError("the baseline can't be downgraded")
