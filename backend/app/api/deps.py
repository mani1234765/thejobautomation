"""Request authentication, CSRF protection, rate limiting and ownership helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base, get_db, utcnow
from app.models import ApiToken, RevokedSession, User
from app.security.core import (
    API_TOKEN_PREFIX,
    CSRF_HEADER,
    SESSION_COOKIE,
    csrf_token_for,
    decode_session_token,
    hash_api_token,
    rate_limiter,
)

DB = Annotated[Session, Depends(get_db)]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: User
    via: str  # web | api_token
    scopes: frozenset[str]

    @property
    def actor(self) -> str:
        return "mcp" if self.via == "api_token" else "user"

    @property
    def id(self) -> str:
        return self.user.id


def client_ip(request: Request) -> str:
    """Caller IP for rate limiting and audit.

    With N trusted appending proxies, the right-most N entries of X-Forwarded-For were written by
    infrastructure; the left-most of those is the real client. Anything further left came from the
    client and is ignored, so it can't be spoofed. Run uvicorn with --no-proxy-headers so the TCP
    peer isn't rewritten first.
    """
    peer = request.client.host if request.client else "unknown"
    hops = get_settings().trusted_proxy_hops
    if hops <= 0:
        return peer
    chain = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    return chain[-hops] if len(chain) >= hops else peer


def _from_api_token(db: Session, raw: str) -> Principal | None:
    tok = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_api_token(raw), ApiToken.revoked_at.is_(None)))
    if not tok:
        return None
    user = db.get(User, tok.user_id)
    if not user or not user.is_active:
        return None
    tok.last_used_at = utcnow()
    db.commit()
    return Principal(user, "api_token", frozenset(tok.scopes))


def get_principal(request: Request, db: DB) -> Principal:
    s = get_settings()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].startswith(API_TOKEN_PREFIX):
        principal = _from_api_token(db, auth[7:].strip())
        if not principal:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API token.")
    else:
        token = request.cookies.get(SESSION_COOKIE)
        data = decode_session_token(token) if token else None
        user = db.get(User, data["sub"]) if data else None
        if not user or not user.is_active or user.token_version != data.get("ver"):  # type: ignore[union-attr]
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
        if data.get("jti") and db.get(RevokedSession, data["jti"]):  # type: ignore[union-attr]
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This session has been signed out.")
        # CSRF: mutating cookie-authenticated requests must echo the session-bound token in a header.
        if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != csrf_token_for(token):  # type: ignore[arg-type]
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token.")
        principal = Principal(user, "web", frozenset({"*"}))

    if not rate_limiter.allow(f"api:{principal.id}", s.rate_limit_api_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Slow down a little.")
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_scope(scope: str):
    def _check(p: CurrentPrincipal) -> Principal:
        if "*" not in p.scopes and scope not in p.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"This token lacks the '{scope}' scope.")
        return p

    return _check


def web_only(p: CurrentPrincipal) -> Principal:
    """For actions that must be performed by the person in the JobPilot UI, never by a token."""
    if p.via != "web":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This action is only available in the JobPilot web app.")
    return p


def get_owned[M: Base](db: Session, model: type[M], obj_id: str, user_id: str) -> M:
    """Fetch a user-owned row or 404. Never reveals whether another user's row exists."""
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "user_id", None) != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found.")
    return obj
