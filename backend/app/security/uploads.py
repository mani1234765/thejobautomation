"""Upload validation and private file storage.

Files are validated by size, extension *and* magic bytes (the client-sent content type is
never trusted), then stored under a random key outside any web root.
"""

from __future__ import annotations

import hashlib
import io
import secrets
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"


class UploadRejected(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedFile:
    data: bytes
    file_format: str  # docx | pdf
    mime_type: str
    sha256: str
    safe_name: str


def _safe_filename(name: str) -> str:
    base = Path(name or "resume").name
    cleaned = "".join(c for c in base if c.isalnum() or c in "._- ").strip() or "resume"
    return cleaned[:120]


def validate_resume_upload(filename: str, data: bytes) -> ValidatedFile:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > max_bytes:
        raise UploadRejected(f"The file exceeds the {get_settings().max_upload_mb} MB limit.")

    ext = Path(filename or "").suffix.lower()
    if data.startswith(b"%PDF-"):
        if ext not in ("", ".pdf"):
            raise UploadRejected("File extension does not match its contents.")
        fmt, mime = "pdf", PDF_MIME
    elif data.startswith(b"PK\x03\x04"):
        if ext not in ("", ".docx"):
            raise UploadRejected("File extension does not match its contents.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
                if "word/document.xml" not in names:
                    raise UploadRejected("This archive is not a Word document.")
                if any(n.lower().endswith("vbaproject.bin") for n in names):
                    raise UploadRejected("Macro-enabled documents are not accepted.")
                # Zip-bomb guard: refuse archives that inflate beyond 20x the limit.
                if sum(i.file_size for i in zf.infolist()) > max_bytes * 20:
                    raise UploadRejected("The document is too large when decompressed.")
        except zipfile.BadZipFile as exc:
            raise UploadRejected("The DOCX file is corrupted.") from exc
        fmt, mime = "docx", DOCX_MIME
    else:
        raise UploadRejected("Only PDF and DOCX resumes are supported.")

    return ValidatedFile(
        data=data,
        file_format=fmt,
        mime_type=mime,
        sha256=hashlib.sha256(data).hexdigest(),
        safe_name=_safe_filename(filename),
    )


class FileStore:
    """Local private storage keyed by opaque ids. Swap for object storage in production."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_settings().storage_dir).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("Invalid storage key")
        return p

    def save(self, user_id: str, data: bytes, ext: str) -> str:
        key = f"{user_id}/{secrets.token_hex(16)}.{ext}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_user(self, user_id: str) -> None:
        d = self._path(user_id)
        if d.exists():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()


def get_file_store() -> FileStore:
    return FileStore()
