"""apprenticeship stipend policy settings (Phase 3 M3)

Five columns on `user_settings` for the apprenticeship stipend policy: a minimum (NULL = no minimum, so
nothing changes until a user sets one), its currency (INR) and period (month), how stipends advertised
only in the description are treated (compare with the minimum) and whether an advertised stipend that meets
the minimum passes (pass). Additive only: existing columns and values are unchanged, and existing rows get
the defaults through the column defaults.

Plain ADD/DROP COLUMN (no batch table rebuild), as in d5e7a1c3b9f2.

Revision ID: e8f2b4d6a1c7
Revises: d5e7a1c3b9f2
Create Date: 2026-11-28 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8f2b4d6a1c7"
down_revision: str | Sequence[str] | None = "d5e7a1c3b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> list[sa.Column]:
    return [
        sa.Column("apprentice_min_stipend", sa.Float(), nullable=True),
        sa.Column("apprentice_stipend_currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("apprentice_stipend_period", sa.String(length=5), server_default="month", nullable=False),
        sa.Column("apprentice_advertised_stipend", sa.String(length=8), server_default="compare", nullable=False),
        sa.Column("apprentice_unverified_outcome", sa.String(length=6), server_default="pass", nullable=False),
    ]


def upgrade() -> None:
    for column in _columns():
        op.add_column("user_settings", column)


def downgrade() -> None:
    for column in reversed(_columns()):
        op.drop_column("user_settings", column.name)
