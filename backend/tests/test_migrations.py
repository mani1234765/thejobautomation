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


def test_job_extraction_fields_are_additive(migration_url):
    """d5e7a1c3b9f2: four nullable columns on jobs. Existing rows are untouched and the new facts start empty
    (NULL = not yet extracted); downgrade removes only those columns."""
    from sqlalchemy import text

    _alembic(migration_url, "upgrade", "c4d2e8f1a9b3")
    engine = create_engine(migration_url)
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO companies (id, normalized_name, name, created_at, updated_at) "
                "VALUES ('c1', 'acme', 'Acme', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        c.execute(
            text(
                "INSERT INTO jobs (id, provider, external_id, source_key, company_id, title, normalized_title, "
                "work_mode, employment_type, is_internship, experience_min, salary_disclosure, description_text, "
                "required_skills, preferred_skills, url, fingerprint, first_seen_at, last_seen_at, created_at, "
                "updated_at) VALUES ('j1', 'greenhouse', '1', 'greenhouse:acme', 'c1', 'Engineer', 'engineer', "
                "'hybrid', 'full_time', false, 3, 'undisclosed', 'Python', '[\"Python\"]', '[]', 'https://x', 'f', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # A decision referencing the job: on SQLite a table rebuild of `jobs` fails (or cascades) on these.
        c.execute(
            text(
                "INSERT INTO users (id, email, password_hash, token_version, is_active, created_at, updated_at) "
                "VALUES ('u1', 'u1@example.com', 'x', 0, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        c.execute(
            text(
                "INSERT INTO job_matches (id, user_id, job_id, score, breakdown, eligibility, scoring_version, "
                "profile_hash, computed_at) VALUES ('m1', 'u1', 'j1', 70, '{}', '{}', '2026.11.4', 'h', "
                "CURRENT_TIMESTAMP)"
            )
        )
    cols = "title, work_mode, employment_type, experience_min, required_skills"

    def matches():
        with engine.connect() as c:
            return c.execute(text("SELECT id, job_id, score FROM job_matches")).all()

    def row(extra: str = ""):
        with engine.connect() as c:
            sql = f"SELECT {cols}{extra} FROM jobs WHERE id = 'j1'"  # noqa: S608 - fixed column names only
            return tuple(c.execute(text(sql)).one())

    before = row()
    _alembic(migration_url, "upgrade", "d5e7a1c3b9f2")
    new = ", experience_preferred_min, mentioned_skills, field_evidence, remote_region"
    assert row(new)[:5] == before and row(new)[5:] == (None, None, None, None)
    assert matches() == [("m1", "j1", 70)]
    _alembic(migration_url, "downgrade", "c4d2e8f1a9b3")
    assert row() == before and matches() == [("m1", "j1", 70)]
    tables = set(inspect(engine).get_table_names())
    assert not {t for t in tables if t.startswith("_alembic_tmp")}
    assert "remote_region" not in {c["name"] for c in inspect(engine).get_columns("jobs")}
    engine.dispose()


def test_apprenticeship_settings_are_additive(migration_url):
    """e8f2b4d6a1c7: five columns on user_settings. Existing settings keep their values; the new ones take
    their defaults (no apprenticeship minimum, INR, monthly, compare advertised stipends, pass when met)."""
    from sqlalchemy import text

    _alembic(migration_url, "upgrade", "d5e7a1c3b9f2")
    engine = create_engine(migration_url)
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users (id, email, password_hash, token_version, is_active, created_at, updated_at) "
                "VALUES ('u1', 'u1@example.com', 'x', 0, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        c.execute(
            text(
                "INSERT INTO user_settings (id, user_id, min_ctc_lpa, allow_undisclosed_salary, min_stipend_monthly_inr, "
                "allow_undisclosed_stipend, daily_application_limit, require_approval, min_match_score, "
                "notify_in_app, notify_email, notify_whatsapp, daily_summary, created_at, updated_at) VALUES "
                "('s1', 'u1', 12.5, false, 15000, false, 40, true, 70, true, false, false, true, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    old = "min_ctc_lpa, allow_undisclosed_salary, min_stipend_monthly_inr, allow_undisclosed_stipend, daily_application_limit"
    new = (
        "apprentice_min_stipend, apprentice_stipend_currency, apprentice_stipend_period, "
        "apprentice_advertised_stipend, apprentice_unverified_outcome"
    )

    def row(cols: str):
        with engine.connect() as c:
            sql = f"SELECT {cols} FROM user_settings WHERE id = 's1'"  # noqa: S608 - fixed column names only
            return tuple(c.execute(text(sql)).one())

    before = row(old)
    _alembic(migration_url, "upgrade", "e8f2b4d6a1c7")
    assert row(old) == before and row(new) == (None, "INR", "month", "compare", "pass")
    _alembic(migration_url, "downgrade", "d5e7a1c3b9f2")
    assert row(old) == before
    assert "apprentice_min_stipend" not in {c["name"] for c in inspect(engine).get_columns("user_settings")}
    engine.dispose()
