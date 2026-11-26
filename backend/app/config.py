"""Centralised, environment-driven configuration.

Every setting is read from the environment (or `.env`) with the `JOBPILOT_` prefix, except
`DATABASE_URL`. Settings are validated at boot so a misconfigured deployment fails fast.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOBPILOT_", env_file=(BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"), extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    database_url: str = Field(
        default=f"sqlite:///{BACKEND_DIR / 'data' / 'jobpilot.db'}", validation_alias="DATABASE_URL"
    )
    secret_key: str = Field(default="dev-insecure-secret-key-change-me-0000000000", min_length=32)
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt integration credentials at rest.
    encryption_key: str | None = None
    storage_dir: Path = BACKEND_DIR / "data" / "files"
    public_app_url: str = "http://localhost:3000"

    session_ttl_hours: int = 24 * 7
    cookie_secure: bool = False
    max_upload_mb: int = 8
    rate_limit_auth_per_minute: int = 10
    rate_limit_api_per_minute: int = 240
    # Shared limiter store. Required in production so limits hold across API replicas.
    redis_url: str | None = None

    # AI provider (provider-neutral). Leave key empty to run without AI features.
    llm_provider: Literal["openai", "anthropic", "none"] = "none"
    llm_model: str = ""
    llm_api_key: str | None = None
    llm_base_url: str | None = None  # any OpenAI-compatible endpoint
    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = 16000
    # Anthropic effort level (low|medium|high|xhigh|max). Set explicitly: Opus 5.5 defaults to medium.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_daily_budget_usd: float = 2.0
    # Comma-separated models users may pick as a per-account override (the default model is always allowed).
    llm_allowed_models: str | None = None

    # Integrations (optional; each is reported as "requires setup" when unset).
    google_client_id: str | None = None
    google_client_secret: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None

    job_source_timeout_seconds: float = 20.0
    worker_poll_seconds: float = 3.0

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.env == "production":
            weak = ("dev-insecure", "change-me", "changeme", "secret", "example")
            if any(w in self.secret_key.lower() for w in weak) or len(set(self.secret_key)) < 16:
                raise ValueError("JOBPILOT_SECRET_KEY must be a long random value in production (not a placeholder)")
            if not self.encryption_key:
                raise ValueError("JOBPILOT_ENCRYPTION_KEY must be set in production")
            from cryptography.fernet import Fernet

            try:
                Fernet(self.encryption_key.encode())
            except ValueError as exc:
                raise ValueError(
                    "JOBPILOT_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64 bytes)"
                ) from exc
            if not self.cookie_secure:
                raise ValueError("JOBPILOT_COOKIE_SECURE must be true in production")
            if not self.redis_url:
                raise ValueError("JOBPILOT_REDIS_URL must be set in production (shared rate limiting)")
        return self

    @property
    def ai_configured(self) -> bool:
        return self.llm_provider != "none" and bool(self.llm_api_key) and bool(self.llm_model)

    @property
    def gmail_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self.whatsapp_access_token and self.whatsapp_phone_number_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
