from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, IdMixin, TimestampMixin, UTCDateTime
from app.domain.salary_policy import DEFAULT_MIN_CTC_LPA, MAX_DAILY_APPLICATIONS


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    profile: Mapped[UserProfile] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    settings: Mapped[UserSettings] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


class UserProfile(IdMixin, TimestampMixin, Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    location: Mapped[str | None] = mapped_column(String(200))
    headline: Mapped[str | None] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    work_authorization: Mapped[str | None] = mapped_column(String(300))
    career_objectives: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    portfolio_url: Mapped[str | None] = mapped_column(String(500))
    target_titles: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    work_modes: Mapped[list[str]] = mapped_column(JSON, default=list)  # remote | hybrid | onsite
    experience_levels: Mapped[list[str]] = mapped_column(JSON, default=list)
    employment_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    industries: Mapped[list[str]] = mapped_column(JSON, default=list)
    years_experience: Mapped[float | None] = mapped_column(Float)
    salary_expectation_lpa: Mapped[float | None] = mapped_column(Float)
    notice_period: Mapped[str | None] = mapped_column(String(100))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    user: Mapped[User] = relationship(back_populates="profile")


class UserSettings(IdMixin, TimestampMixin, Base):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    # Salary policy (full-time) — see domain/salary_policy.py
    min_ctc_lpa: Mapped[float] = mapped_column(Float, default=DEFAULT_MIN_CTC_LPA)
    allow_undisclosed_salary: Mapped[bool] = mapped_column(Boolean, default=True)
    # Internship stipend policy is separate from full-time CTC.
    min_stipend_monthly_inr: Mapped[float | None] = mapped_column(Float)
    allow_undisclosed_stipend: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_application_limit: Mapped[int] = mapped_column(Integer, default=MAX_DAILY_APPLICATIONS)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    min_match_score: Mapped[int] = mapped_column(Integer, default=55)
    ai_model_override: Mapped[str | None] = mapped_column(String(120))
    notify_in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_number: Mapped[str | None] = mapped_column(String(40))
    daily_summary: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="settings")


class ApiToken(IdMixin, TimestampMixin, Base):
    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Skill(IdMixin, TimestampMixin, Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))  # canonical taxonomy name
    category: Mapped[str | None] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(20), default="user")  # resume | user
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)  # snippets where the skill was found


class ReviewMixin:
    """Provenance and review state for profile entries extracted from a resume.

    `fingerprint` identifies the extracted entry across re-extractions; `user_edited` marks a correction
    that re-extraction must never overwrite. Entries the user adds by hand have no fingerprint.
    """

    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    confidence: Mapped[float | None] = mapped_column(Float)
    review_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    fingerprint: Mapped[str | None] = mapped_column(String(32))


class Experience(ReviewMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "experiences"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[str | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(20))  # professional | internship | freelance
    employer: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    bullets: Mapped[list[str]] = mapped_column(JSON, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Project(ReviewMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[str | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(20))  # academic | personal | professional
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    technologies: Mapped[list[str]] = mapped_column(JSON, default=list)
    url: Mapped[str | None] = mapped_column(String(500))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Education(ReviewMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "education"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[str | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    institution: Mapped[str] = mapped_column(String(300))
    degree: Mapped[str | None] = mapped_column(String(200))
    field: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    grade: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Certification(ReviewMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "certifications"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[str | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(300))
    issuer: Mapped[str | None] = mapped_column(String(200))
    issued_on: Mapped[date | None] = mapped_column(Date)
    details: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class RevokedSession(Base):
    """Session token ids revoked at logout; rows are purged once the token would have expired anyway."""

    __tablename__ = "revoked_sessions"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class AuditEvent(IdMixin, Base):
    __tablename__ = "audit_events"

    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(20))  # user | system | mcp | automation
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(32))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class Notification(IdMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deliveries: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Integration(IdMixin, TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))  # gmail | whatsapp
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    account_label: Mapped[str | None] = mapped_column(String(200))
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    oauth_state: Mapped[str | None] = mapped_column(String(64))
