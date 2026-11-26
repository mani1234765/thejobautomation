from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, IdMixin, TimestampMixin, UTCDateTime


class Resume(IdMixin, TimestampMixin, Base):
    __tablename__ = "resumes"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    file_format: Mapped[str] = mapped_column(String(10))  # docx | pdf
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | parsed | failed
    parse_error: Mapped[str | None] = mapped_column(Text)
    parsed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    parser: Mapped[str | None] = mapped_column(String(40))  # heuristic | heuristic+llm
    extracted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    text_content: Mapped[str | None] = mapped_column(Text)
    # Review state of the corrected profile built from this resume:
    # {"dismissed": [fingerprints the user deleted or merged away], "dismissed_skills": [...],
    #  "summary_applied": bool, "reviewed_at": iso, "last_reextracted_at": iso}
    review: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")

    sections: Mapped[list[ResumeSection]] = relationship(
        back_populates="resume", cascade="all, delete-orphan", order_by="ResumeSection.ordinal"
    )
    versions: Mapped[list[ResumeVersion]] = relationship(back_populates="resume", cascade="all, delete-orphan")


class ResumeSection(IdMixin, Base):
    __tablename__ = "resume_sections"

    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    raw_heading: Mapped[str | None] = mapped_column(String(200))
    canonical_type: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    # Source references: paragraph ids ("b12", "t0r1c0p0", "h0p1") into the original document.
    paragraph_ids: Mapped[list[str]] = mapped_column(JSON, default=list)

    resume: Mapped[Resume] = relationship(back_populates="sections")


class ResumeVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "resume_versions"

    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    # [{id, paragraph_id, section, original, proposed, rationale, kind, status, flags}]
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_by: Mapped[str] = mapped_column(String(40))  # deterministic | llm:<model>
    storage_key: Mapped[str | None] = mapped_column(String(300))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    resume: Mapped[Resume] = relationship(back_populates="versions")
