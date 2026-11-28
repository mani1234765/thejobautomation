"""job extraction fields (Phase 3 M3)

Four nullable columns on `jobs` for facts the M3 extractor derives from listings: the preferred (not required)
years of experience, skills a listing only mentions, the quoted wording behind each extracted fact, and the region
a remote role may be done from. Additive only: no existing column or row is changed. Existing jobs get the new
facts on their board's next sync.

Revision ID: d5e7a1c3b9f2
Revises: c4d2e8f1a9b3
Create Date: 2026-11-27 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5e7a1c3b9f2"
down_revision: str | Sequence[str] | None = "c4d2e8f1a9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> list[sa.Column]:
    return [
        sa.Column("experience_preferred_min", sa.Float(), nullable=True),
        sa.Column("mentioned_skills", sa.JSON(), nullable=True),
        sa.Column("field_evidence", sa.JSON(), nullable=True),
        sa.Column("remote_region", sa.String(length=120), nullable=True),
    ]


# Plain ADD/DROP COLUMN, never a batch table rebuild: on SQLite a rebuild drops and recreates `jobs`, which
# applications, decisions and saved jobs reference. Both PostgreSQL and SQLite (3.35+) alter these plain
# nullable columns in place.
def upgrade() -> None:
    for column in _columns():
        op.add_column("jobs", column)


def downgrade() -> None:
    for column in reversed(_columns()):
        op.drop_column("jobs", column.name)
