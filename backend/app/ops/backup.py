"""Backup and restore for JobPilot data: the database plus the private file store.

Usage:
    python -m app.ops.backup create  --out /backups               # writes jobpilot-<UTC>.tar.gz
    python -m app.ops.backup restore --archive /backups/jobpilot-....tar.gz [--target-url URL] [--files-dir DIR]
    python -m app.ops.backup verify  --archive /backups/jobpilot-....tar.gz

PostgreSQL uses pg_dump custom format (-Fc) and pg_restore; SQLite uses the online backup API, so both
are consistent snapshots taken without stopping the app. Each archive carries a manifest with SHA-256
checksums that `verify` and `restore` check before touching anything. Credentials are taken from the
URL/environment and never written to the manifest or logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.config import get_settings

PG_BIN = os.environ.get("JOBPILOT_PG_BIN", "")  # optional directory holding pg_dump/pg_restore


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _redact(url: str) -> str:
    p = urlsplit(url)
    host = p.hostname or ""
    return f"{p.scheme}://{host}{':' + str(p.port) if p.port else ''}{p.path}"


def _pg_url(url: str) -> str:
    """libpq accepts postgresql:// but not SQLAlchemy's postgresql+psycopg://."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _tool(name: str) -> str:
    path = shutil.which(name, path=PG_BIN or None) or shutil.which(name)
    if not path:
        raise SystemExit(f"{name} not found. Install the PostgreSQL client tools or set JOBPILOT_PG_BIN.")
    return path


def create(out_dir: Path, url: str | None = None, files_dir: Path | None = None) -> Path:
    s = get_settings()
    url = url or s.database_url
    files_dir = files_dir or s.storage_dir
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if url.startswith("sqlite"):
            src = sqlite3.connect(url.split("///", 1)[1])
            dst = sqlite3.connect(work / "database.sqlite")
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            db_file, kind = work / "database.sqlite", "sqlite"
        else:
            db_file, kind = work / "database.dump", "postgresql"
            subprocess.run(
                [_tool("pg_dump"), "-Fc", "--no-owner", "--no-privileges", "-f", str(db_file), _pg_url(url)],
                check=True,
                capture_output=True,
            )
        files_tar = work / "files.tar"
        with tarfile.open(files_tar, "w") as tf:
            if files_dir.exists():
                tf.add(files_dir, arcname="files")
        manifest = {
            "created_at": stamp,
            "database_kind": kind,
            "database_source": _redact(url),
            "checksums": {db_file.name: _sha256(db_file), files_tar.name: _sha256(files_tar)},
        }
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2))
        archive = out_dir / f"jobpilot-{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            for name in ("manifest.json", db_file.name, files_tar.name):
                tf.add(work / name, arcname=name)
    os.chmod(archive, 0o600)
    return archive


def _extract(archive: Path, dest: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(dest, filter="data")
    manifest = json.loads((dest / "manifest.json").read_text())
    for name, digest in manifest["checksums"].items():
        if _sha256(dest / name) != digest:
            raise SystemExit(f"Checksum mismatch for {name}: the archive is corrupt or was modified.")
    return manifest


def verify(archive: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return _extract(archive, Path(tmp))


def restore(archive: Path, target_url: str | None = None, files_dir: Path | None = None) -> dict:
    s = get_settings()
    target_url = target_url or s.database_url
    files_dir = files_dir or s.storage_dir
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        manifest = _extract(archive, work)
        if manifest["database_kind"] == "sqlite":
            if not target_url.startswith("sqlite"):
                raise SystemExit("This archive holds a SQLite database; restore it into a sqlite:/// URL.")
            shutil.copyfile(work / "database.sqlite", target_url.split("///", 1)[1])
        else:
            if target_url.startswith("sqlite"):
                raise SystemExit("This archive holds a PostgreSQL dump; restore it into a PostgreSQL URL.")
            subprocess.run(
                [
                    _tool("pg_restore"),
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--no-privileges",
                    "--single-transaction",
                    "-d",
                    _pg_url(target_url),
                    str(work / "database.dump"),
                ],
                check=True,
                capture_output=True,
            )
        with tarfile.open(work / "files.tar") as tf:
            staging = work / "restored"
            tf.extractall(staging, filter="data")
        if (staging / "files").exists():
            files_dir.parent.mkdir(parents=True, exist_ok=True)
            if files_dir.exists():
                shutil.rmtree(files_dir)
            shutil.copytree(staging / "files", files_dir)
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--out", type=Path, required=True)
    for name in ("restore", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--archive", type=Path, required=True)
        if name == "restore":
            p.add_argument("--target-url")
            p.add_argument("--files-dir", type=Path)
    args = ap.parse_args()
    if args.cmd == "create":
        print(create(args.out))
    elif args.cmd == "verify":
        m = verify(args.archive)
        print(f"OK: {m['database_kind']} backup from {m['created_at']}, checksums verified")
    else:
        m = restore(args.archive, args.target_url, args.files_dir)
        print(f"Restored {m['database_kind']} backup from {m['created_at']}")


if __name__ == "__main__":
    main()
