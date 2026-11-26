"""Alembic environment: the database URL comes from JobPilot settings (DATABASE_URL), never alembic.ini."""

from logging.config import fileConfig

from alembic import context

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.db import Base, get_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Render app-level column types as their plain SQLAlchemy storage type, so migrations never import app code."""
    from app.db import UTCDateTime

    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(url=str(get_engine().url), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with get_engine().connect() as connection:
        # Batch mode lets ALTER TABLE migrations work on SQLite as well as PostgreSQL.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True,
                          compare_type=True, render_item=render_item)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
