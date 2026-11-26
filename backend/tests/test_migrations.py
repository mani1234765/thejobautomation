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
