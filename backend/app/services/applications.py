"""Application lifecycle: preparation, approval and submission tracking.

Invariants enforced here (not in the UI):
* one application per user per job (DB unique constraint) and one idempotency key per application;
* nothing reaches `submitted` without an approved ApprovalRequest whose snapshot matches the
  application as it is now — any edit after approval voids it;
* submissions are recorded as `attempted` until acknowledged; failures are never shown as success;
* the daily application limit is enforced when a submission is started.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import service as ai
from app.ai.guards import untrusted
from app.ai.provider import AIUnavailable, LLMError
from app.db import utcnow
from app.domain.application_state import InvalidTransition, check_transition
from app.domain.salary_policy import MAX_DAILY_APPLICATIONS
from app.models import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    ApprovalRequest,
    Education,
    Experience,
    Interview,
    Job,
    Resume,
    ResumeVersion,
    User,
    UserSettings,
)
from app.services import audit


class ApplicationError(ValueError):
    pass


def _event(
    db: Session,
    app: Application,
    kind: str,
    actor: str,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        ApplicationEvent(
            application_id=app.id,
            user_id=app.user_id,
            kind=kind,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            detail=detail or {},
            created_at=utcnow(),
        )
    )


def _move(
    db: Session, app: Application, target: str, actor: str, *, guarded_ok: bool = False, detail: dict | None = None
) -> None:
    check_transition(app.status, target, guarded_ok=guarded_ok)
    _event(db, app, "status_change", actor, from_status=app.status, to_status=target, detail=detail)
    audit.record(
        db,
        user_id=app.user_id,
        actor=actor,
        action="application.status_changed",
        entity_type="application",
        entity_id=app.id,
        meta={"from": app.status, "to": target},
    )
    app.status, app.status_changed_at = target, utcnow()


def create(db: Session, user: User, job: Job, *, status: str = "shortlisted", actor: str) -> Application:
    existing = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    if existing:
        return existing
    if status not in ("discovered", "shortlisted", "preparing"):
        raise ApplicationError("New applications start as discovered, shortlisted or preparing.")
    now = utcnow()
    app = Application(
        user_id=user.id,
        job_id=job.id,
        status=status,
        status_changed_at=now,
        created_by=actor,
        idempotency_key=hashlib.sha256(f"{user.id}:{job.id}".encode()).hexdigest(),
    )
    db.add(app)
    try:
        db.flush()
    except IntegrityError:  # concurrent create — return the winner
        db.rollback()
        return db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    _event(db, app, "status_change", actor, to_status=status, detail={"created": True})
    audit.record(
        db,
        user_id=user.id,
        actor=actor,
        action="application.created",
        entity_type="application",
        entity_id=app.id,
        meta={"job_id": job.id},
    )
    db.commit()
    return app


def transition(db: Session, app: Application, target: str, actor: str, note: str | None = None) -> Application:
    try:
        _move(db, app, target, actor, detail={"note": note} if note else None)
    except InvalidTransition as exc:
        raise ApplicationError(str(exc)) from exc
    if target in ("withdrawn", "preparing", "shortlisted"):
        _void_pending_approvals(db, app, "Application moved back to " + target)
    db.commit()
    return app


# ── Preparation ────────────────────────────────────────────────────────────────────────────────
def _profile_answers(db: Session, user: User) -> list[tuple[str, str | None, str]]:
    """Standard application questions answered strictly from stored, user-provided data."""
    p = user.profile
    latest = db.scalar(
        select(Experience)
        .where(Experience.user_id == user.id, Experience.category != "internship")
        .order_by(Experience.is_current.desc(), Experience.start_date.desc())
    )
    edu = db.scalar(select(Education).where(Education.user_id == user.id).order_by(Education.end_date.desc()))
    yrs = f"{p.years_experience:g}" if p.years_experience is not None else None
    return [
        ("Full name", p.full_name, "profile.full_name"),
        ("Email", user.email, "account.email"),
        ("Phone number", p.phone, "profile.phone"),
        ("Current location", p.location, "profile.location"),
        ("LinkedIn profile", p.linkedin_url, "profile.linkedin_url"),
        ("Portfolio / website", p.portfolio_url, "profile.portfolio_url"),
        ("Work authorization", p.work_authorization, "profile.work_authorization"),
        ("Total years of professional experience", yrs, "profile.years_experience"),
        ("Current or most recent employer", latest.employer if latest else None, "experience"),
        ("Current or most recent job title", latest.title if latest else None, "experience"),
        ("Highest education", " — ".join(x for x in (edu.degree, edu.institution) if x) if edu else None, "education"),
        (
            "Expected CTC (LPA)",
            f"{p.salary_expectation_lpa:g}" if p.salary_expectation_lpa else None,
            "profile.salary_expectation_lpa",
        ),
        ("Notice period", p.notice_period, "profile.notice_period"),
    ]


def _profile_context(db: Session, user: User) -> str:
    p = user.profile
    exps = db.scalars(select(Experience).where(Experience.user_id == user.id)).all()
    lines = [
        f"Name: {p.full_name}",
        f"Headline: {p.headline or ''}",
        f"Summary: {p.summary or ''}",
        f"Years of experience: {p.years_experience}",
        f"Career objectives: {p.career_objectives or ''}",
    ]
    for e in exps:
        lines.append(
            f"- [{e.category}] {e.title or ''} at {e.employer} ({e.start_date}–{'present' if e.is_current else e.end_date}): "
            + " ".join(e.bullets[:6])
        )
    return "\n".join(lines)


def prepare(
    db: Session,
    user: User,
    app: Application,
    *,
    resume_id: str | None,
    resume_version_id: str | None,
    questions: list[str],
    generate_cover_letter: bool,
    actor: str,
) -> dict[str, Any]:
    if app.status in ("submitted", "assessment", "interview", "offer", "rejected", "withdrawn"):
        raise ApplicationError("This application can no longer be prepared.")
    notes: list[str] = []
    if resume_version_id:
        v = db.get(ResumeVersion, resume_version_id)
        if not v or v.user_id != user.id:
            raise ApplicationError("Resume version not found.")
        if v.status != "approved":
            raise ApplicationError("Approve the tailored resume before attaching it.")
        app.resume_version_id, app.resume_id = v.id, v.resume_id
    elif resume_id:
        r = db.get(Resume, resume_id)
        if not r or r.user_id != user.id:
            raise ApplicationError("Resume not found.")
        app.resume_id, app.resume_version_id = r.id, None
    elif not app.resume_id:
        primary = db.scalar(select(Resume).where(Resume.user_id == user.id, Resume.is_primary.is_(True)))
        if primary:
            app.resume_id = primary.id

    existing = {a.question for a in app.answers}
    for q, value, source in _profile_answers(db, user):
        if q in existing:
            continue
        app.answers.append(
            ApplicationAnswer(
                question=q,
                answer=value,
                confidence="verified" if value else "unknown",
                needs_review=value is None,
                source=source,
            )
        )

    custom = [q.strip() for q in questions if q.strip() and q.strip() not in existing]
    ai_ok = ai.status()["configured"]
    job = app.job
    if custom:
        drafted: dict[str, dict] = {}
        if ai_ok:
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["answers"],
                "properties": {
                    "answers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["question", "answer", "known"],
                            "properties": {
                                "question": {"type": "string"},
                                "answer": {"type": "string"},
                                "known": {"type": "boolean"},
                            },
                        },
                    }
                },
            }
            try:
                res = ai.complete(
                    db,
                    user,
                    feature="application_answers",
                    system="You draft job application answers.",
                    json_schema=schema,
                    messages=[
                        {
                            "role": "user",
                            "content": "Draft answers using only the candidate profile. If the profile doesn't contain "
                            "the information, set known=false and answer with an empty string.\n\n"
                            f"Candidate profile:\n{_profile_context(db, user)}\n\n"
                            f"Job: {job.title} at {job.company.name}\n"
                            f"{untrusted('job_description', job.description_text, 8000)}\n\n"
                            "Questions:\n" + "\n".join(f"- {q}" for q in custom),
                        }
                    ],
                )
                drafted = {a["question"].strip(): a for a in (res.data or {}).get("answers", [])}
            except (AIUnavailable, LLMError) as exc:
                notes.append(f"AI drafting unavailable: {exc}")
        else:
            notes.append("Custom questions need your answer — connect an AI provider to get drafts.")
        for q in custom:
            d = drafted.get(q)
            known = bool(d and d.get("known") and d.get("answer"))
            app.answers.append(
                ApplicationAnswer(
                    question=q,
                    answer=d["answer"] if known else None,  # type: ignore[index]
                    confidence="inferred" if known else "unknown",
                    needs_review=True,
                    source="ai_draft" if known else None,
                )
            )

    if generate_cover_letter:
        if not ai_ok:
            notes.append("Cover letter generation needs an AI provider. You can write one below.")
        else:
            try:
                res = ai.complete(
                    db,
                    user,
                    feature="cover_letter",
                    system="You write concise, specific cover letters.",
                    messages=[
                        {
                            "role": "user",
                            "content": "Write a 180–250 word cover letter. Use only facts from the profile; don't "
                            "claim skills or results that aren't there. No placeholders.\n\n"
                            f"Profile:\n{_profile_context(db, user)}\n\nJob: {job.title} at {job.company.name}\n"
                            f"{untrusted('job_description', job.description_text, 8000)}",
                        }
                    ],
                )
                app.cover_letter, app.cover_letter_generated_by = res.text.strip(), f"llm:{res.model}"
            except (AIUnavailable, LLMError) as exc:
                notes.append(f"Cover letter not generated: {exc}")

    if app.status in ("discovered", "shortlisted"):
        _move(db, app, "preparing", actor)
    elif app.status in ("awaiting_approval", "approved", "failed"):
        _void_pending_approvals(db, app, "Application was re-prepared")
        if app.status != "preparing":
            _move(db, app, "preparing", actor, detail={"reason": "re-prepared after changes"})
    _event(db, app, "document", actor, detail={"prepared": True, "questions": len(custom)})
    db.commit()
    return {"notes": notes}


def update_answer(db: Session, app: Application, answer_id: str, text: str, actor: str) -> ApplicationAnswer:
    ans = next((a for a in app.answers if a.id == answer_id), None)
    if ans is None:
        raise ApplicationError("Answer not found.")
    if app.status not in ("preparing", "awaiting_approval", "approved", "failed"):
        raise ApplicationError("Answers can't be edited at this stage.")
    ans.answer, ans.edited_by_user, ans.needs_review = text.strip() or None, True, False
    ans.confidence = "verified" if ans.answer else "unknown"
    if app.status in ("awaiting_approval", "approved", "failed"):
        _void_pending_approvals(db, app, "An answer was edited")
        _move(db, app, "preparing", actor, detail={"reason": "edited after approval request"})
    db.commit()
    return ans


def update_cover_letter(db: Session, app: Application, text: str | None, actor: str) -> None:
    app.cover_letter = (text or "").strip() or None
    app.cover_letter_generated_by = "user" if app.cover_letter else None
    if app.status in ("awaiting_approval", "approved", "failed"):
        _void_pending_approvals(db, app, "Cover letter was edited")
        _move(db, app, "preparing", actor)
    db.commit()


# ── Approval ───────────────────────────────────────────────────────────────────────────────────
def snapshot(app: Application) -> dict[str, Any]:
    return {
        "job_id": app.job_id,
        "resume_id": app.resume_id,
        "resume_version_id": app.resume_version_id,
        "cover_letter_sha": hashlib.sha256((app.cover_letter or "").encode()).hexdigest(),
        "answers": sorted([[a.question, a.answer] for a in app.answers], key=lambda x: x[0]),
    }


def _snapshot_hash(snap: dict) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True, default=str).encode()).hexdigest()


def readiness(app: Application) -> list[str]:
    problems = []
    if not app.resume_id:
        problems.append("Attach a resume.")
    unresolved = [a.question for a in app.answers if a.needs_review]
    if unresolved:
        problems.append(f"Review {len(unresolved)} answer(s): " + ", ".join(unresolved[:4]))
    if app.job.closed_at:
        problems.append("This posting has been closed by the employer.")
    return problems


def request_approval(db: Session, user: User, app: Application, actor: str) -> ApprovalRequest:
    problems = readiness(app)
    if problems:
        raise ApplicationError(" ".join(problems))
    if app.status != "preparing":
        raise ApplicationError("Only applications being prepared can be sent for approval.")
    _void_pending_approvals(db, app, "Superseded by a new request")
    snap = snapshot(app)
    req = ApprovalRequest(user_id=user.id, application_id=app.id, requested_by=actor, snapshot=snap)
    db.add(req)
    _move(db, app, "awaiting_approval", actor)
    audit.notify(
        db,
        user_id=user.id,
        kind="approval_requested",
        title=f"Approve application: {app.job.title}",
        body=f"{app.job.company.name} — review the resume and answers, then approve or reject.",
        link=f"/applications/{app.id}",
    )
    db.commit()
    return req


def _void_pending_approvals(db: Session, app: Application, reason: str) -> None:
    for r in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.application_id == app.id, ApprovalRequest.status.in_(("pending", "approved"))
        )
    ):
        r.status, r.decision_note = "expired", reason


def decide(
    db: Session,
    user: User,
    req: ApprovalRequest,
    *,
    approve: bool,
    via: str,
    actor: str,
    note: str | None = None,
    scheduled_for: datetime | None = None,
) -> ApprovalRequest:
    if via != "web":
        # Defence in depth: only a human in the JobPilot UI may decide (the route also enforces this).
        raise ApplicationError("Approvals can only be decided by you in JobPilot, not by an API token or AI client.")
    if req.status != "pending":
        raise ApplicationError(f"This approval request is already {req.status}.")
    app = db.get(Application, req.application_id)
    assert app is not None
    if _snapshot_hash(snapshot(app)) != _snapshot_hash(req.snapshot):
        req.status = "expired"
        db.commit()
        raise ApplicationError("The application changed after approval was requested. Request approval again.")
    req.decided_at, req.decided_via, req.decision_note = utcnow(), via, note
    if approve:
        req.status, req.scheduled_for = "approved", scheduled_for
        _move(db, app, "approved", actor, guarded_ok=True, detail={"via": via})
    else:
        req.status = "rejected"
        _move(db, app, "preparing", actor, detail={"via": via, "reason": note or "approval rejected"})
    audit.record(
        db,
        user_id=user.id,
        actor=actor,
        action="approval." + req.status,
        entity_type="approval_request",
        entity_id=req.id,
        meta={"via": via, "application_id": app.id},
    )
    db.commit()
    return req


# ── Submission tracking ────────────────────────────────────────────────────────────────────────
def daily_limit(user: User) -> int:
    """The user's limit, within 1–MAX_DAILY_APPLICATIONS even if a stored value is out of range."""
    from app.services.policy_settings import effective

    return min(int(effective(user.settings).values["daily_application_limit"]), MAX_DAILY_APPLICATIONS)


def lock_submission_quota(db: Session, user: User) -> None:
    """Take a row lock on the user's settings row for the rest of the transaction (PostgreSQL
    `SELECT … FOR UPDATE`). Concurrent submission starts for the same user wait here until the first
    commits, so each one counts the others' committed starts. SQLite ignores FOR UPDATE but allows only
    one writer at a time, which is sufficient for the single-process development setup."""
    db.execute(select(UserSettings.id).where(UserSettings.user_id == user.id).with_for_update())


def submissions_last_24h(db: Session, user_id: str) -> int:
    since = utcnow() - timedelta(hours=24)
    return (
        db.scalar(
            select(func.count())
            .select_from(ApplicationEvent)
            .where(
                ApplicationEvent.user_id == user_id,
                ApplicationEvent.kind == "submission",
                ApplicationEvent.created_at >= since,
                ApplicationEvent.detail["action"].as_string() == "start",
            )
        )
        or 0
    )


def record_submission(
    db: Session,
    user: User,
    app: Application,
    action: str,
    actor: str,
    *,
    reference: str | None = None,
    reason: str | None = None,
) -> Application:
    approved = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.application_id == app.id, ApprovalRequest.status == "approved")
    )
    if action == "start":
        # Serialise submission starts per user, then re-read state under the lock: the count-and-insert
        # below is then atomic, so simultaneous requests can't exceed the limit or double-start an app.
        lock_submission_quota(db, user)
        db.refresh(app)
        if app.submission_state == "attempted":
            return app  # idempotent: a retry of "start" never creates a second attempt
        if app.status not in ("approved", "failed") or approved is None:
            raise ApplicationError("Approve this application before submitting it.")
        if _snapshot_hash(snapshot(app)) != _snapshot_hash(approved.snapshot):
            raise ApplicationError("The application changed after it was approved. Request approval again.")
        limit = daily_limit(user)
        if submissions_last_24h(db, user.id) >= limit:
            raise ApplicationError(f"Daily limit of {limit} submissions reached. Try again later or raise the limit.")
        if app.status == "failed":
            _move(db, app, "approved", actor, guarded_ok=True, detail={"retry": True})
        app.submission_state, app.submission_method = "attempted", "assisted"
        _event(
            db,
            app,
            "submission",
            actor,
            detail={"action": "start", "method": "assisted", "attempt": secrets.token_hex(4)},
        )
    elif action == "confirm":
        if app.submission_state not in ("attempted", "unknown"):
            raise ApplicationError("Start the submission before confirming it.")
        app.submission_state, app.acknowledged_at = "acknowledged", utcnow()
        app.submitted_at = app.submitted_at or utcnow()
        app.submission_reference = (reference or "")[:300] or None
        app.follow_up_at = app.follow_up_at or utcnow() + timedelta(days=7)
        _move(
            db,
            app,
            "submitted",
            actor,
            guarded_ok=True,
            detail={"acknowledgement": "user_confirmed", "reference": app.submission_reference},
        )
        _event(db, app, "submission", actor, detail={"action": "acknowledged", "source": "user_confirmed"})
        audit.notify(
            db,
            user_id=user.id,
            kind="application_submitted",
            title=f"Submitted: {app.job.title}",
            body=f"{app.job.company.name}. A follow-up reminder is set for 7 days.",
            link=f"/applications/{app.id}",
        )
    elif action == "fail":
        if app.submission_state != "attempted":
            raise ApplicationError("There's no submission attempt to mark as failed.")
        app.submission_state = "failed"
        _move(db, app, "failed", actor, detail={"reason": reason})
        _event(db, app, "submission", actor, detail={"action": "failed", "reason": reason})
        audit.notify(
            db,
            user_id=user.id,
            kind="application_failed",
            title=f"Submission failed: {app.job.title}",
            body=reason or "Marked as failed. You can retry from the application page.",
            link=f"/applications/{app.id}",
        )
    elif action == "unknown":
        if app.submission_state != "attempted":
            raise ApplicationError("There's no submission attempt to update.")
        app.submission_state = "unknown"
        _event(db, app, "submission", actor, detail={"action": "unknown"})
    else:
        raise ApplicationError("Unknown submission action.")
    audit.record(
        db,
        user_id=user.id,
        actor=actor,
        action=f"application.submission_{action}",
        entity_type="application",
        entity_id=app.id,
    )
    db.commit()
    return app


def add_note(db: Session, app: Application, text: str, actor: str) -> None:
    _event(db, app, "note", actor, detail={"text": text[:2000]})
    db.commit()


def schedule_interview(
    db: Session,
    user: User,
    app: Application,
    *,
    stage: str,
    scheduled_at: datetime | None,
    mode: str | None,
    link: str | None,
    notes: str | None,
    actor: str,
) -> Interview:
    if app.status not in ("submitted", "assessment", "interview"):
        raise ApplicationError("Interviews can be added once an application is submitted.")
    iv = Interview(
        user_id=user.id,
        application_id=app.id,
        stage=stage,
        scheduled_at=scheduled_at,
        mode=mode,
        location_or_link=link,
        notes=notes,
    )
    db.add(iv)
    if app.status != "interview":
        _move(db, app, "interview", actor)
    _event(
        db,
        app,
        "interview",
        actor,
        detail={"stage": stage, "scheduled_at": scheduled_at.isoformat() if scheduled_at else None},
    )
    db.commit()
    return iv
