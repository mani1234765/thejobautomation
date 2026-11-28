"""Automation runs. Automations discover and prepare; they never submit.

* discovery   — sync the user's job boards, score new postings, notify about strong matches and
                shortlist the best ones (bounded by `max_shortlist`).
* preparation — for shortlisted applications above the score threshold, attach the primary resume,
                fill profile-backed answers and request the user's approval (bounded by the daily limit).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import job_filters as jf
from app.models import Application, AutomationConfig, AutomationExecution, Job, JobMatch, JobSource, User
from app.services import applications as app_svc
from app.services import audit
from app.services import jobs as jobs_svc

DEFAULT_FILTERS: dict[str, dict[str, Any]] = {
    "discovery": {
        "min_score": 70,
        "notify_min_score": 75,
        "max_shortlist": 5,
        "locations": [],
        "work_modes": [],
        "experience_levels": [],
        "employment_types": [],
        "title_keywords": [],
    },
    "preparation": {"min_score": 70, "max_per_run": 3},
}


class ExecutionLog:
    def __init__(self, execution: AutomationExecution) -> None:
        self.execution = execution
        self.lines: list[dict[str, Any]] = []

    def __call__(self, level: str, event: str, **fields: Any) -> None:
        self.lines.append({"ts": utcnow().isoformat(), "level": level, "event": event, **fields})
        self.execution.log = list(self.lines)


def _listing_filters(f: dict[str, Any], log: ExecutionLog) -> dict[str, list[str]] | None:
    """The saved multiple-choice filters, read without failing on older or hand-made values: a single string
    counts as one value; unrecognised values are logged and ignored. None when a filter was set but none of its
    values is recognised: it then matches no job, as it did before, rather than silently matching every job."""
    out = {}
    for name in jf.CHOICES:
        known, unknown = jf.stored_choices(name, f.get(name))
        if unknown:
            log("warn", "filter_values_ignored", filter=name, values=unknown)
        if unknown and not known:
            return None
        out[name] = known
    return out


def _passes(job: Job, f: dict[str, Any], listing: dict[str, list[str]]) -> bool:
    """Same listing rules as job search: an unstated employment type, work mode or experience is included."""
    if not (
        jf.employment_ok(job, listing["employment_types"])
        and jf.work_mode_ok(job, listing["work_modes"])
        and jf.experience_ok(job, listing["experience_levels"])
    ):
        return False
    if f.get("locations"):
        wanted = [loc.lower() for loc in f["locations"]]
        remote_ok = job.work_mode == "remote" and "remote" in wanted
        if not remote_ok and not any(loc in (job.location or "").lower() for loc in wanted):
            return False
    return not f.get("title_keywords") or any(k.lower() in job.title.lower() for k in f["title_keywords"])


def run_discovery(db: Session, user: User, cfg: AutomationConfig | None, log: ExecutionLog) -> dict[str, Any]:
    f = {**DEFAULT_FILTERS["discovery"], **(cfg.filters if cfg else {})}
    sources = db.scalars(select(JobSource).where(JobSource.user_id == user.id, JobSource.enabled.is_(True))).all()
    if not sources:
        log("warn", "no_sources", message="No job boards followed — add sources in Discover Jobs.")
    started = utcnow()
    stats = {"sources": len(sources), "source_errors": 0, "new_jobs": 0, "strong_matches": 0, "shortlisted": 0}
    for s in sources:
        res = jobs_svc.sync_source(db, s, actor="automation")
        if res.status == "error":
            stats["source_errors"] += 1
            log("error", "source_failed", source=s.source_key, error=res.error)
        else:
            stats["new_jobs"] += res.created
            log(
                "info",
                "source_synced",
                source=s.source_key,
                fetched=res.fetched,
                created=res.created,
                closed=res.closed,
            )
    jobs_svc.ensure_matches(db, user)
    new = db.execute(
        select(Job, JobMatch)
        .join(JobMatch, (JobMatch.job_id == Job.id) & (JobMatch.user_id == user.id))
        .where(Job.first_seen_at >= started - timedelta(seconds=5), Job.duplicate_of_id.is_(None))
        .order_by(JobMatch.score.desc())
    ).all()
    listing = _listing_filters(f, log)
    if listing is None:
        log("warn", "filters_match_nothing", message="A saved filter has no recognised values; edit the automation.")
    strong = [
        (j, m)
        for j, m in new
        if listing is not None
        and m.score >= f["notify_min_score"]
        and m.eligibility.get("status") != "ineligible"
        and _passes(j, f, listing)
    ]
    stats["strong_matches"] = len(strong)
    if strong:
        top = ", ".join(f"{j.title} at {j.company.name} ({m.score})" for j, m in strong[:3])
        audit.notify(
            db,
            user_id=user.id,
            kind="new_matches",
            title=f"{len(strong)} new strong matches",
            body=top,
            link="/jobs?sort=match",
        )
    for j, m in strong[: f["max_shortlist"]]:
        if m.score >= f["min_score"] and not db.scalar(
            select(Application.id).where(Application.user_id == user.id, Application.job_id == j.id)
        ):
            app_svc.create(db, user, j, status="shortlisted", actor="automation")
            stats["shortlisted"] += 1
            log("info", "shortlisted", job_id=j.id, score=m.score)
    db.commit()
    return stats


def run_preparation(db: Session, user: User, cfg: AutomationConfig | None, log: ExecutionLog) -> dict[str, Any]:
    f = {**DEFAULT_FILTERS["preparation"], **(cfg.filters if cfg else {})}
    remaining = max(0, app_svc.daily_limit(user) - app_svc.submissions_last_24h(db, user.id))
    budget = min(int(f["max_per_run"]), remaining)
    stats = {"prepared": 0, "approval_requested": 0, "skipped": 0, "limit_remaining": remaining}
    if budget == 0:
        log("info", "daily_limit_reached", limit=app_svc.daily_limit(user))
        return stats
    rows = db.execute(
        select(Application, JobMatch)
        .join(JobMatch, (JobMatch.job_id == Application.job_id) & (JobMatch.user_id == user.id))
        .where(Application.user_id == user.id, Application.status == "shortlisted", JobMatch.score >= f["min_score"])
        .order_by(JobMatch.score.desc())
    ).all()
    for app, m in rows[:budget]:
        if m.eligibility.get("status") == "ineligible":
            stats["skipped"] += 1
            log("info", "skipped_ineligible", application_id=app.id, reasons=m.eligibility.get("blocks"))
            continue
        app_svc.prepare(
            db,
            user,
            app,
            resume_id=None,
            resume_version_id=None,
            questions=[],
            generate_cover_letter=False,
            actor="automation",
        )
        stats["prepared"] += 1
        problems = app_svc.readiness(app)
        if problems:
            log("info", "needs_user_input", application_id=app.id, problems=problems)
            continue
        app_svc.request_approval(db, user, app, "automation")
        stats["approval_requested"] += 1
        log("info", "approval_requested", application_id=app.id)
    return stats


RUNNERS = {"discovery": run_discovery, "preparation": run_preparation}


def execute(db: Session, user: User, kind: str, cfg: AutomationConfig | None, trigger: str) -> AutomationExecution:
    ex = AutomationExecution(
        user_id=user.id,
        config_id=cfg.id if cfg else None,
        kind=kind,
        trigger=trigger,
        status="running",
        started_at=utcnow(),
        log=[],
    )
    db.add(ex)
    db.commit()
    log = ExecutionLog(ex)
    log("info", "started", kind=kind, trigger=trigger)
    try:
        stats = RUNNERS[kind](db, user, cfg, log)
        ex.stats = stats
        ex.status = "partial" if stats.get("source_errors") else "succeeded"
        log("info", "finished", **stats)
    except Exception as exc:  # noqa: BLE001 - recorded on the execution and surfaced in the UI
        db.rollback()
        ex = db.get(AutomationExecution, ex.id)  # type: ignore[assignment]
        ex.status, ex.error = "failed", f"{exc.__class__.__name__}: {exc}"[:2000]
        log.execution = ex
        log("error", "failed", error=ex.error)
        audit.notify(
            db,
            user_id=user.id,
            kind="automation_failed",
            title=f"Automation failed: {kind}",
            body=ex.error[:200],
            link="/automation",
        )
    ex.finished_at = utcnow()
    if cfg:
        cfg.last_run_at = ex.finished_at
    db.commit()
    return ex


def schedule_next(cfg: AutomationConfig) -> None:
    cfg.next_run_at = utcnow() + timedelta(minutes=cfg.interval_minutes) if cfg.state == "active" else None
