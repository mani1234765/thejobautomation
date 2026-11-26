"""Migrations apply cleanly from empty, round-trip down/up, and match the models (no drift)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.db import Base

BACKEND = Path(__file__).resolve().parent.parent


def _alembic(url: str, *args: str) -> str:
    env = {**os.environ, "DATABASE_URL": url}
    out = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout + out.stderr


@pytest.fixture
def migration_url(tmp_path):
    url = os.environ.get("JOBPILOT_TEST_MIGRATION_DATABASE_URL")  # dedicated, disposable Postgres DB in CI
    if url:
        engine = create_engine(url)
        with engine.begin() as c:
            Base.metadata.drop_all(c)
            c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        engine.dispose()
        return url
    return f"sqlite:///{tmp_path / 'migrations.db'}"


def test_upgrade_downgrade_roundtrip_and_no_drift(migration_url):
    import app.models  # noqa: F401

    _alembic(migration_url, "upgrade", "head")
    engine = create_engine(migration_url)
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "No new upgrade operations detected" in _alembic(migration_url, "check")

    _alembic(migration_url, "downgrade", "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}

    _alembic(migration_url, "upgrade", "head")
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_existing_daily_limits_on_the_old_default_are_raised(migration_url):
    """c4d2e8f1a9b3: users still on the old default (10) move to 10,000; other saved values are kept."""
    from sqlalchemy import text

    _alembic(migration_url, "upgrade", "b7c65986ac58")
    engine = create_engine(migration_url)
    with engine.begin() as c:
        for uid, limit in (("u10", 10), ("u3", 3), ("u50", 50)):
            c.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, token_version, is_active, created_at, updated_at) "
                    "VALUES (:id, :email, 'x', 0, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": uid, "email": f"{uid}@example.com"},
            )
            c.execute(
                text(
                    "INSERT INTO user_settings (id, user_id, min_ctc_lpa, allow_undisclosed_salary, "
                    "allow_undisclosed_stipend, daily_application_limit, require_approval, min_match_score, "
                    "notify_in_app, notify_email, notify_whatsapp, daily_summary, created_at, updated_at) VALUES "
                    "(:id, :uid, 8, true, true, :limit, true, 55, true, false, false, true, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": f"s{uid}", "uid": uid, "limit": limit},
            )

    def limits():
        with engine.connect() as c:
            return dict(c.execute(text("SELECT user_id, daily_application_limit FROM user_settings")).all())

    _alembic(migration_url, "upgrade", "c4d2e8f1a9b3")
    assert limits() == {"u10": 10_000, "u3": 3, "u50": 50}
    _alembic(migration_url, "downgrade", "b7c65986ac58")
    assert limits() == {"u10": 10, "u3": 3, "u50": 50}
    engine.dispose()
