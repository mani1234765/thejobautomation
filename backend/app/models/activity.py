"""Automation, background tasks, communications, AI and interview-practice records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, IdMixin, TimestampMixin, UTCDateTime


class AutomationConfig(IdMixin, TimestampMixin, Base):
    __tablename__ = "automation_configs"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # discovery | preparation
    interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(10), default="active")  # active | paused | stopped
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AutomationExecution(IdMixin, Base):
    __tablename__ = "automation_executions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[str | None] = mapped_column(ForeignKey("automation_configs.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    trigger: Mapped[str] = mapped_column(String(20))  # schedule | manual | mcp
    status: Mapped[str] = mapped_column(String(12))  # running | succeeded | failed | partial
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)


class Task(IdMixin, TimestampMixin, Base):
    """Durable background task queue row."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_ready", "status", "run_after"),)

    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime())
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Communication(IdMixin, TimestampMixin, Base):
    __tablename__ = "communications"
    __table_args__ = (UniqueConstraint("user_id", "channel", "external_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # email | linkedin | whatsapp | note
    external_id: Mapped[str] = mapped_column(String(200))
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound | draft
    sender: Mapped[str | None] = mapped_column(String(300))
    recipient: Mapped[str | None] = mapped_column(String(300))
    subject: Mapped[str | None] = mapped_column(String(500))
    snippet: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(30))
    classified_by: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class InterviewSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "interview_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300))
    # [{id, category, question, rationale, provenance, answer?, feedback?}]
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(12), default="active")


class AIConversation(IdMixin, TimestampMixin, Base):
    __tablename__ = "ai_conversations"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))


class AIMessage(IdMixin, Base):
    __tablename__ = "ai_messages"

    conversation_id: Mapped[str] = mapped_column(ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Exact provider content (incl. thinking blocks) replayed unchanged on later turns.
    provider: Mapped[str | None] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(120))
    raw: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class AIUsage(IdMixin, Base):
    __tablename__ = "ai_usage"

    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(120))
    feature: Mapped[str] = mapped_column(String(40))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    # True when cost_usd is a conservative estimate (model not in the price table).
    cost_estimated: Mapped[bool] = mapped_column(default=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
