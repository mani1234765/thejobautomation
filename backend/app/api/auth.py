from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import DB, CurrentPrincipal, Principal, client_ip, web_only
from app.config import get_settings
from app.db import utcnow
from app.models import ApiToken, RevokedSession, User, UserProfile, UserSettings
from app.security.core import (
    SESSION_COOKIE,
    burn_password_check,
    create_session_token,
    csrf_token_for,
    decode_session_token,
    hash_password,
    new_api_token,
    rate_limiter,
    verify_password,
)
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])
CSRF_COOKIE = "jp_csrf"
TOKEN_SCOPES = ("read", "jobs:write", "resumes:write", "applications:write", "approvals:write", "automation:write")


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class Registration(Credentials):
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=256)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        classes = sum((any(c.isalpha() for c in v), any(c.isdigit() for c in v), any(not c.isalnum() for c in v)))
        if classes < 2 or len(set(v)) < 5:
            raise ValueError("Use at least 10 characters mixing letters with numbers or symbols.")
        return v


class MeOut(BaseModel):
    id: str
    email: str
    full_name: str | None
    onboarding_completed: bool
    created_at: datetime


def _me(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.profile.full_name if user.profile else None,
        onboarding_completed=user.onboarding_completed_at is not None,
        created_at=user.created_at,
    )


def _set_session(response: Response, user: User) -> None:
    s = get_settings()
    token = create_session_token(user.id, user.token_version)
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure, samesite="lax", path="/"
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token_for(token),
        max_age=max_age,
        httponly=False,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )


def _auth_rate_limit(request: Request, email: str) -> None:
    limit = get_settings().rate_limit_auth_per_minute
    # Fails closed: if the shared limiter is unreachable, authentication is refused rather than unthrottled.
    if not (
        rate_limiter.allow(f"auth-ip:{client_ip(request)}", limit, fail_closed=True)
        and rate_limiter.allow(f"auth-email:{email.lower()}", limit, fail_closed=True)
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a minute.")


@router.post("/register", response_model=MeOut, status_code=201)
def register(body: Registration, request: Request, response: Response, db: DB) -> MeOut:
    _auth_rate_limit(request, body.email)
    email = body.email.lower()
    if db.scalar(select(func.count()).select_from(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
    user = User(email=email, password_hash=hash_password(body.password))
    user.profile = UserProfile(full_name=body.full_name.strip())
    user.settings = UserSettings()
    db.add(user)
    db.flush()
    audit.record(db, user_id=user.id, actor="user", action="auth.register", ip=client_ip(request))
    db.commit()
    _set_session(response, user)
    return _me(user)


@router.post("/login", response_model=MeOut)
def login(body: Credentials, request: Request, response: Response, db: DB) -> MeOut:
    _auth_rate_limit(request, body.email)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        burn_password_check(body.password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    if not verify_password(user.password_hash, body.password) or not user.is_active:
        audit.record(db, user_id=user.id, actor="user", action="auth.login_failed", ip=client_ip(request))
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    audit.record(db, user_id=user.id, actor="user", action="auth.login", ip=client_ip(request))
    db.commit()
    _set_session(response, user)
    return _me(user)


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DB) -> None:
    """Revokes this session server-side (not just the cookie), so a copied token stops working too."""
    token = request.cookies.get(SESSION_COOKIE)
    data = decode_session_token(token) if token else None
    if data and data.get("jti") and not db.get(RevokedSession, data["jti"]):
        db.add(RevokedSession(jti=data["jti"], expires_at=datetime.fromtimestamp(data["exp"], tz=UTC)))
        audit.record(db, user_id=data.get("sub"), actor="user", action="auth.logout")
        db.commit()
    _clear_cookies(response)


@router.post("/logout-all", status_code=204)
def logout_all(response: Response, db: DB, p: Principal = Depends(web_only)) -> None:
    p.user.token_version += 1
    audit.record(db, user_id=p.id, actor="user", action="auth.logout_all")
    db.commit()
    _clear_cookies(response)


@router.get("/me", response_model=MeOut)
def me(p: CurrentPrincipal) -> MeOut:
    return _me(p.user)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, response: Response, db: DB, p: Principal = Depends(web_only)) -> None:
    if not verify_password(p.user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    p.user.password_hash = hash_password(body.new_password)
    p.user.token_version += 1  # sign out other sessions
    audit.record(db, user_id=p.id, actor="user", action="auth.password_changed")
    db.commit()
    _set_session(response, p.user)


# ── Personal API tokens (used by the MCP server) ───────────────────────────────────────────────
class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["read"])

    @field_validator("scopes")
    @classmethod
    def _valid(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in TOKEN_SCOPES]
        if bad:
            raise ValueError(f"Unknown scopes: {', '.join(bad)}")
        return sorted(set(v) | {"read"})


class TokenOut(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    token: str | None = None  # only returned once, at creation


@router.get("/tokens", response_model=list[TokenOut])
def list_tokens(db: DB, p: Principal = Depends(web_only)) -> list[TokenOut]:
    rows = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == p.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return [
        TokenOut(
            id=t.id, name=t.name, prefix=t.prefix, scopes=t.scopes, created_at=t.created_at, last_used_at=t.last_used_at
        )
        for t in rows
    ]


@router.post("/tokens", response_model=TokenOut, status_code=201)
def create_token(body: TokenCreate, db: DB, p: Principal = Depends(web_only)) -> TokenOut:
    raw, prefix, digest = new_api_token()
    tok = ApiToken(user_id=p.id, name=body.name, prefix=prefix, token_hash=digest, scopes=body.scopes)
    db.add(tok)
    db.flush()
    audit.record(
        db,
        user_id=p.id,
        actor="user",
        action="api_token.created",
        entity_type="api_token",
        entity_id=tok.id,
        meta={"scopes": body.scopes},
    )
    db.commit()
    return TokenOut(
        id=tok.id,
        name=tok.name,
        prefix=prefix,
        scopes=tok.scopes,
        created_at=tok.created_at,
        last_used_at=None,
        token=raw,
    )


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: str, db: DB, p: Principal = Depends(web_only)) -> None:
    tok = db.get(ApiToken, token_id)
    if not tok or tok.user_id != p.id:
        raise HTTPException(404, "Token not found.")
    tok.revoked_at = utcnow()
    audit.record(db, user_id=p.id, actor="user", action="api_token.revoked", entity_type="api_token", entity_id=tok.id)
    db.commit()
