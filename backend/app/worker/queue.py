"""Durable DB-backed task queue with idempotency keys, retries and exponential backoff."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Task

STALE_LOCK = timedelta(minutes=15)


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    user_id: str | None = None,
    idempotency_key: str | None = None,
    delay_seconds: float = 0,
    max_attempts: int = 3,
) -> Task | None:
    """Insert a task. With an idempotency key, a duplicate enqueue is a no-op (returns None)."""
    if idempotency_key and db.scalar(select(Task.id).where(Task.idempotency_key == idempotency_key)):
        return None
    task = Task(
        kind=kind,
        payload=payload,
        user_id=user_id,
        idempotency_key=idempotency_key,
        run_after=utcnow() + timedelta(seconds=delay_seconds),
        max_attempts=max_attempts,
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return task


def claim(db: Session) -> Task | None:
    """Claim one ready task. Uses SKIP LOCKED on Postgres so multiple workers never double-run a task."""
    now = utcnow()
    q = (
        select(Task)
        .where(
            Task.run_after <= now,
            or_(Task.status == "queued", (Task.status == "running") & (Task.locked_at < now - STALE_LOCK)),
        )
        .order_by(Task.run_after)
        .limit(1)
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    task = db.scalar(q)
    if task is None:
        return None
    task.status, task.locked_at, task.attempts = "running", now, task.attempts + 1
    db.commit()
    return task


def complete(db: Session, task: Task, result: dict[str, Any] | None = None) -> None:
    task.status, task.result, task.locked_at, task.last_error = "done", result or {}, None, None
    db.commit()


def fail(db: Session, task: Task, error: str, *, retryable: bool = True) -> None:
    task.last_error, task.locked_at = error[:2000], None
    if retryable and task.attempts < task.max_attempts:
        task.status = "queued"
        task.run_after = utcnow() + timedelta(seconds=30 * 2 ** (task.attempts - 1))
    else:
        task.status = "failed"
    db.commit()
