from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, IdMixin, TimestampMixin, UTCDateTime
from app.models.job import Job


class Application(IdMixin, TimestampMixin, Base):
    __tablename__ = "applications"
    # One application per user per job: the database itself prevents duplicates.
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resume_version_id: Mapped[str | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    resume_id: Mapped[str | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    cover_letter: Mapped[str | None] = mapped_column(Text)
    cover_letter_generated_by: Mapped[str | None] = mapped_column(String(60))
    submission_state: Mapped[str] = mapped_column(String(16), default="not_submitted")
    submission_method: Mapped[str | None] = mapped_column(String(30))  # assisted | provider_api
    submission_reference: Mapped[str | None] = mapped_column(String(300))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    follow_up_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(20), default="user")

    job: Mapped[Job] = relationship(lazy="joined")
    events: Mapped[list[ApplicationEvent]] = relationship(
        cascade="all, delete-orphan", order_by="ApplicationEvent.created_at"
    )
    answers: Mapped[list[ApplicationAnswer]] = relationship(
        cascade="all, delete-orphan", order_by="ApplicationAnswer.created_at"
    )


class ApplicationEvent(IdMixin, Base):
    __tablename__ = "application_events"

    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # status_change | note | submission | document
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(20))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class ApplicationDocument(IdMixin, TimestampMixin, Base):
    __tablename__ = "application_documents"

    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # resume | cover_letter | other
    resume_version_id: Mapped[str | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    label: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str | None] = mapped_column(String(300))


class ApplicationAnswer(IdMixin, TimestampMixin, Base):
    __tablename__ = "application_answers"

    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(10))  # verified | inferred | unknown
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(200))  # which profile field / evidence
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)


class ApprovalRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(String(20))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decided_via: Mapped[str | None] = mapped_column(String(20))  # web | mcp
    decision_note: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime())
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # what exactly was approved


class Interview(IdMixin, TimestampMixin, Base):
    __tablename__ = "interviews"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(60))
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    mode: Mapped[str | None] = mapped_column(String(30))
    location_or_link: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(20))
