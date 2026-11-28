from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, IdMixin, TimestampMixin, UTCDateTime


class JobSource(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("user_id", "provider", "identifier"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # greenhouse | lever | ashby
    identifier: Mapped[str] = mapped_column(String(120))  # board token / site name
    display_name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_status: Mapped[str | None] = mapped_column(String(20))  # ok | error
    last_error: Mapped[str | None] = mapped_column(Text)
    last_job_count: Mapped[int | None] = mapped_column(Integer)

    @property
    def source_key(self) -> str:
        return f"{self.provider}:{self.identifier}"


class Company(IdMixin, TimestampMixin, Base):
    __tablename__ = "companies"

    normalized_name: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(300))
    size: Mapped[str | None] = mapped_column(String(40))
    industry: Mapped[str | None] = mapped_column(String(100))


class Job(IdMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        Index("ix_jobs_source_key_seen", "source_key", "last_seen_at"),
    )

    provider: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(120))
    source_key: Mapped[str] = mapped_column(String(150), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    normalized_title: Mapped[str] = mapped_column(String(300), index=True)
    department: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(300))
    work_mode: Mapped[str | None] = mapped_column(String(10))  # remote | hybrid | onsite
    remote_region: Mapped[str | None] = mapped_column(String(120))  # informational: where a remote role may be done
    # full_time | part_time | contract | internship | freelance | temporary | apprenticeship
    employment_type: Mapped[str | None] = mapped_column(String(20))
    is_internship: Mapped[bool] = mapped_column(Boolean, default=False)
    seniority: Mapped[str | None] = mapped_column(String(20))
    experience_min: Mapped[float | None] = mapped_column(Float)
    experience_max: Mapped[float | None] = mapped_column(Float)
    experience_preferred_min: Mapped[float | None] = mapped_column(Float)  # "3+ years preferred": not a requirement
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str | None] = mapped_column(String(10))  # year | month | hour
    salary_disclosure: Mapped[str] = mapped_column(String(12), default="undisclosed")  # undisclosed|advertised|verified
    salary_text: Mapped[str | None] = mapped_column(String(300))
    description_text: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str | None] = mapped_column(Text)
    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list)  # mandatory
    preferred_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    mentioned_skills: Mapped[list[str] | None] = mapped_column(JSON)  # NULL until the job is next synced
    # Quoted wording behind each extracted fact (skills with tier and term, experience, work mode, region, type).
    field_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    url: Mapped[str] = mapped_column(String(600))
    apply_url: Mapped[str | None] = mapped_column(String(600))
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    duplicate_of_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    company: Mapped[Company] = relationship(lazy="joined")


class JobMatch(IdMixin, Base):
    __tablename__ = "job_matches"
    __table_args__ = (UniqueConstraint("user_id", "job_id"), Index("ix_job_matches_user_score", "user_id", "score"))

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    eligibility: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scoring_version: Mapped[str] = mapped_column(String(20))
    profile_hash: Mapped[str] = mapped_column(String(64))
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SavedJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "saved_jobs"
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(12))  # saved | dismissed
    note: Mapped[str | None] = mapped_column(Text)
