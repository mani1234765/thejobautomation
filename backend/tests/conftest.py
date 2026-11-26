import os

os.environ["JOBPILOT_ENV"] = "test"
os.environ["JOBPILOT_LLM_PROVIDER"] = "none"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import db as dbmod
from app.config import get_settings
from app.db import Base
from app.security.core import rate_limiter

TEST_DATABASE_URL = os.environ.get("JOBPILOT_TEST_DATABASE_URL")  # e.g. postgresql+psycopg://localhost/jobpilot_test


@pytest.fixture(scope="session")
def _engine():
    """SQLite in memory by default; a real PostgreSQL database when JOBPILOT_TEST_DATABASE_URL is set."""
    import app.models  # noqa: F401

    if TEST_DATABASE_URL:
        engine = create_engine(TEST_DATABASE_URL)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, _engine):
    monkeypatch.setenv("JOBPILOT_STORAGE_DIR", str(tmp_path / "files"))
    get_settings.cache_clear()
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    dbmod.set_engine(_engine)
    rate_limiter.reset()
    yield _engine
    get_settings.cache_clear()


@pytest.fixture
def db(isolated):
    s = dbmod.session_factory()()
    yield s
    s.close()


class Api:
    """TestClient wrapper that sends the CSRF header automatically, like the frontend does."""

    def __init__(self, client: TestClient):
        self.c = client

    def _h(self, extra=None):
        h = {"x-jobpilot-csrf": self.c.cookies.get("jp_csrf", "")}
        h.update(extra or {})
        return h

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(kw.pop("headers", None)), **kw)


def make_client() -> Api:
    from app.main import create_app

    return Api(TestClient(create_app()))


def register(api: Api, email="asha@example.com", password="correct-horse-42", name="Asha Verma"):
    r = api.post("/api/v1/auth/register", json={"email": email, "password": password, "full_name": name})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def api():
    return make_client()


@pytest.fixture
def user_api(api):
    register(api)
    return api
