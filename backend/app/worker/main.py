"""Background worker: schedules due automations, runs queued tasks and delivers external notifications.

Run with:  uv run python -m app.worker.main
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_factory, utcnow
from app.integrations.whatsapp import WhatsAppError, send_text
from app.logging import configure_logging
from app.models import (
    ApprovalRequest,
    AutomationConfig,
    Interview,
    Job,
    JobMatch,
    Notification,
    RevokedSession,
    Task,
    User,
    UserSettings,
)
from app.services import audit, automation
from app.services import jobs as jobs_svc
from app.worker import queue

log = logging.getLogger("jobpilot.worker")
_running = True

EXTERNAL_KINDS = {
    "approval_requested",
    "application_submitted",
    "application_failed",
    "email_interview_invitation",
    "automation_failed",
    "daily_summary",
}


def schedule_due(db: Session) -> int:
    n = 0
    for cfg in db.scalars(
        select(AutomationConfig).where(AutomationConfig.state == "active", AutomationConfig.next_run_at <= utcnow())
    ):
        # The key includes the scheduled slot, so a crash/restart can never enqueue the same run twice.
        key = f"automation:{cfg.id}:{cfg.next_run_at.isoformat() if cfg.next_run_at else 'now'}"
        if queue.enqueue(
            db,
            "automation",
            {"config_id": cfg.id, "trigger": "schedule"},
            user_id=cfg.user_id,
            idempotency_key=key,
            max_attempts=2,
        ):
            n += 1
        automation.schedule_next(cfg)
        db.commit()
    return n


def handle(db: Session, task: Task) -> dict[str, Any]:
    if task.kind == "automation":
        cfg = db.get(AutomationConfig, task.payload["config_id"])
        if cfg is None or cfg.state != "active" and task.payload.get("trigger") == "schedule":
            return {"skipped": "inactive"}
        user = db.get(User, cfg.user_id)
        assert user is not None
        ex = automation.execute(db, user, cfg.kind, cfg, task.payload.get("trigger", "schedule"))
        return {"execution_id": ex.id, "status": ex.status}
    raise ValueError(f"Unknown task kind {task.kind}")


def deliver_notifications(db: Session) -> int:
    """Send enabled external channels for notifications that haven't been delivered yet."""
    s = get_settings()
    sent = 0
    pending = db.scalars(
        select(Notification)
        .where(Notification.kind.in_(EXTERNAL_KINDS))
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    for n in pending:
        if "whatsapp" in (n.deliveries or {}):
            continue
        user = db.get(User, n.user_id)
        prefs = user.settings if user else None
        if not prefs or not prefs.notify_whatsapp or not prefs.whatsapp_number:
            n.deliveries = {**(n.deliveries or {}), "whatsapp": {"status": "disabled"}}
            continue
        if not s.whatsapp_configured:
            n.deliveries = {**(n.deliveries or {}), "whatsapp": {"status": "not_configured"}}
            continue
        try:
            mid = send_text(prefs.whatsapp_number, f"JobPilot — {n.title}\n{n.body or ''}")
            n.deliveries = {**(n.deliveries or {}), "whatsapp": {"status": "sent", "id": mid}}
            sent += 1
        except WhatsAppError as exc:
            n.deliveries = {**(n.deliveries or {}), "whatsapp": {"status": "failed", "error": str(exc)[:200]}}
    db.commit()
    return sent


def daily_summaries(db: Session) -> int:
    """One summary per opted-in user per 24h, built only from stored records (nothing is sent if empty)."""
    since = utcnow() - timedelta(hours=24)
    made = 0
    for user in db.scalars(
        select(User).join(UserSettings).where(UserSettings.daily_summary.is_(True), User.is_active.is_(True))
    ):
        if db.scalar(
            select(Notification.id).where(
                Notification.user_id == user.id, Notification.kind == "daily_summary", Notification.created_at >= since
            )
        ):
            continue
        jobs_svc.ensure_matches(db, user)  # scores are computed lazily; make sure they're current
        approvals = (
            db.scalar(
                select(func.count())
                .select_from(ApprovalRequest)
                .where(ApprovalRequest.user_id == user.id, ApprovalRequest.status == "pending")
            )
            or 0
        )
        new_jobs = (
            db.scalar(
                select(func.count())
                .select_from(Job)
                .join(JobMatch, JobMatch.job_id == Job.id)
                .where(
                    JobMatch.user_id == user.id,
                    Job.first_seen_at >= since,
                    JobMatch.score >= user.settings.min_match_score,
                )
            )
            or 0
        )
        interviews = (
            db.scalar(
                select(func.count())
                .select_from(Interview)
                .where(
                    Interview.user_id == user.id,
                    Interview.scheduled_at >= utcnow(),
                    Interview.scheduled_at <= utcnow() + timedelta(hours=48),
                )
            )
            or 0
        )
        if not (approvals or new_jobs or interviews):
            continue
        parts = [
            f"{new_jobs} new matching job{'s' * (new_jobs != 1)}" if new_jobs else "",
            f"{approvals} approval{'s' * (approvals != 1)} waiting" if approvals else "",
            f"{interviews} interview{'s' * (interviews != 1)} in the next 48h" if interviews else "",
        ]
        audit.notify(
            db,
            user_id=user.id,
            kind="daily_summary",
            title="Your daily JobPilot summary",
            body=" · ".join(p for p in parts if p),
            link="/overview",
        )
        made += 1
    db.commit()
    return made


def purge_expired(db: Session) -> None:
    db.execute(delete(RevokedSession).where(RevokedSession.expires_at < utcnow()))
    db.commit()


def tick(db: Session) -> bool:
    schedule_due(db)
    task = queue.claim(db)
    if task is not None:
        try:
            result = handle(db, task)
            queue.complete(db, task, result)
            log.info("task_done", extra={"task": task.id, "kind": task.kind})
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            task = db.get(Task, task.id)
            queue.fail(db, task, f"{exc.__class__.__name__}: {exc}")  # type: ignore[arg-type]
            log.exception("task_failed", extra={"task": task.id if task else None})
    daily_summaries(db)
    purge_expired(db)
    deliver_notifications(db)
    return task is not None


def main() -> None:
    configure_logging()

    def stop(*_: Any) -> None:
        global _running
        _running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    log.info("worker_started")
    poll = get_settings().worker_poll_seconds
    while _running:
        with session_factory()() as db:
            busy = tick(db)
        if not busy:
            time.sleep(poll)
    log.info("worker_stopped")


if __name__ == "__main__":
    main()
