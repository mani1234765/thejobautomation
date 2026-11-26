"""Password hashing, session tokens, API tokens, secret encryption and rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.security.rate_limit import rate_limiter  # noqa: F401 - re-exported for existing callers

_hasher = PasswordHasher()  # Argon2id with library-recommended parameters
SESSION_COOKIE = "jp_session"  # noqa: S105 - cookie name
CSRF_HEADER = "x-jobpilot-csrf"
API_TOKEN_PREFIX = "jp_"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# A constant hash so login timing doesn't reveal whether an email exists.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def create_session_token(user_id: str, token_version: int) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(hours=s.session_ttl_hours),
        "typ": "session",
        "jti": secrets.token_hex(16),  # lets a single session be revoked server-side at logout
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_session_token(token: str) -> dict | None:
    try:
        data = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == "session" else None


def new_api_token() -> tuple[str, str, str]:
    """Returns (plaintext, prefix, sha256 hash). Only the hash is stored."""
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:10], hash_api_token(raw)


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def csrf_token_for(session_token: str) -> str:
    """Double-submit CSRF value bound to the session (HMAC so it can't be forged cross-site)."""
    key = get_settings().secret_key.encode()
    return hmac.new(key, session_token.encode(), hashlib.sha256).hexdigest()[:40]


def _fernet() -> Fernet:
    s = get_settings()
    if s.encryption_key:
        return Fernet(s.encryption_key.encode())
    # Development fallback: derive a key from the secret key. Production requires an explicit key.
    digest = hashlib.sha256(("fernet:" + s.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "authorization", "credentials", "cookie"}


def redact(data: dict) -> dict:
    """Recursively mask sensitive keys before anything is logged or audited."""
    out: dict = {}
    for k, v in data.items():
        if any(s in k.lower() for s in SENSITIVE_KEYS):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = redact(v)
        else:
            out[k] = v
    return out
