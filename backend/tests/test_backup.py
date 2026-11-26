"""Backup → disaster → restore drill. Uses real pg_dump/pg_restore when a Postgres URL is provided."""

import os
import tarfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from app import db as dbmod
from app.ops import backup
from tests.conftest import Api, register
from tests.fixtures import build_docx_resume

PG_SOURCE = os.environ.get("JOBPILOT_TEST_BACKUP_SOURCE_URL")  # disposable DBs, e.g. .../jobpilot_backup_src
PG_TARGET = os.environ.get("JOBPILOT_TEST_BACKUP_TARGET_URL")  # and .../jobpilot_backup_dst


@pytest.fixture
def source(tmp_path, monkeypatch):
    """A separate database with real data written through the API."""
    import app.models  # noqa: F401
    from app.config import get_settings

    url = PG_SOURCE or f"sqlite:///{tmp_path / 'src.db'}"
    files = tmp_path / "files"
    monkeypatch.setenv("JOBPILOT_STORAGE_DIR", str(files))
    get_settings.cache_clear()
    engine = create_engine(url)
    dbmod.Base.metadata.drop_all(engine)
    dbmod.Base.metadata.create_all(engine)
    dbmod.set_engine(engine)
    from app.main import create_app

    api = Api(TestClient(create_app()))
    register(api, email="backup@example.com")
    r = api.post("/api/v1/resumes", files={"file": ("cv.docx", build_docx_resume(), "application/octet-stream")})
    assert r.status_code == 201
    yield url, files, engine
    engine.dispose()


def _counts(engine):
    from app.models import AuditEvent, Resume, User

    with engine.connect() as c:
        return tuple(c.execute(select(func.count()).select_from(m)).scalar() for m in (User, Resume, AuditEvent))


def test_backup_restore_drill(source, tmp_path):
    url, files, engine = source
    before = _counts(engine)
    archive = backup.create(tmp_path / "backups", url=url, files_dir=files)
    assert oct(archive.stat().st_mode)[-3:] == "600"
    manifest = backup.verify(archive)
    assert "password" not in str(manifest).lower() and "@" not in manifest["database_source"].split("//")[1]

    # Disaster: wipe the database and the file store.
    dbmod.Base.metadata.drop_all(engine)
    import shutil

    shutil.rmtree(files)
    target = PG_TARGET or url
    backup.restore(archive, target_url=target, files_dir=files)

    restored = create_engine(target)
    assert _counts(restored) == before
    with restored.connect() as c:
        key = c.execute(text("select storage_key from resumes")).scalar()
    assert (files / key).read_bytes() == build_docx_resume()
    restored.dispose()


def test_tampered_archive_is_rejected(source, tmp_path):
    url, files, _ = source
    archive = backup.create(tmp_path / "b", url=url, files_dir=files)
    work = tmp_path / "x"
    with tarfile.open(archive) as tf:
        tf.extractall(work, filter="data")
    with (work / "files.tar").open("ab") as f:
        f.write(b"tamper")
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tf:
        for p in work.iterdir():
            tf.add(p, arcname=p.name)
    with pytest.raises(SystemExit, match="Checksum mismatch"):
        backup.verify(bad)
