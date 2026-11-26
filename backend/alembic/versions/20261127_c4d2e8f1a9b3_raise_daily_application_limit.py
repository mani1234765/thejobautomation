"""raise existing daily application limits to the new default (10,000)

Data-only migration. Users still on the previous default of 10 move to the new default and maximum of
10,000 (owner decision, 2026-11-27). Users who chose any other value keep it. No schema change.

Downgrade returns rows at 10,000 to 10. It can't tell a raised default apart from a user who chose
10,000 themselves, so such users also return to 10.

Revision ID: c4d2e8f1a9b3
Revises: b7c65986ac58
Create Date: 2026-11-27 05:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d2e8f1a9b3"
down_revision: str | Sequence[str] | None = "b7c65986ac58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_DEFAULT = 10
NEW_DEFAULT = 10_000


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE user_settings SET daily_application_limit = :new WHERE daily_application_limit = :old").bindparams(
            new=NEW_DEFAULT, old=OLD_DEFAULT
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE user_settings SET daily_application_limit = :old WHERE daily_application_limit = :new").bindparams(
            new=NEW_DEFAULT, old=OLD_DEFAULT
        )
    )
