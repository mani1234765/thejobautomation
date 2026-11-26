from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, require_scope, web_only
from app.api.jobs import JobCard, to_card
from app.domain.application_state import ACTIVE_PIPELINE, STATUSES, allowed_next
from app.integrations.sources.base import SourceError
from app.models import Application, ApplicationEvent, ApprovalRequest, Interview, JobMatch
from app.services import applications as svc
from app.services import jobs as jobs_svc

router = APIRouter(tags=["applications"])


class AnswerOut(BaseModel):
    id: str
    question: str
    answer: str | None
    confidence: str
    needs_review: bool
    source: str | None
    edited_by_user: bool


class EventOut(BaseModel):
    id: str
    kind: str
    from_status: str | None
    to_status: str | None
    actor: str
    detail: dict[str, Any]
    created_at: datetime


class ApprovalOut(BaseModel):
    id: str
    application_id: str
    status: str
    requested_by: str
    created_at: datetime
    decided_at: datetime | None
    decided_via: str | None
    decision_note: str | None
    scheduled_for: datetime | None
    job_title: str | None = None
    company: str | None = None


class InterviewOut(BaseModel):
    id: str
    stage: str
    scheduled_at: datetime | None
    mode: str | None
    location_or_link: str | None
    notes: str | None
    outcome: str | None


class ApplicationOut(BaseModel):
    id: str
    status: str
    status_changed_at: datetime
    submission_state: str
    submission_method: str | None
    submission_reference: str | None
    submitted_at: datetime | None
    acknowledged_at: datetime | None
    follow_up_at: datetime | None
    resume_id: str | None
    resume_version_id: str | None
    created_at: datetime
    created_by: str
    job: JobCard
    allowed_next: list[str]
    pending_review: int


class ApplicationDetail(ApplicationOut):
    cover_letter: str | None
    cover_letter_generated_by: str | None
    answers: list[AnswerOut]
    events: list[EventOut]
    approvals: list[ApprovalOut]
    interviews: list[InterviewOut]
    readiness: list[str]


def _out(db: DB, app: Application) -> ApplicationOut:
    m = db.scalar(select(JobMatch).where(JobMatch.user_id == app.user_id, JobMatch.job_id == app.job_id))
    return ApplicationOut(
        id=app.id,
        status=app.status,
        status_changed_at=app.status_changed_at,
        submission_state=app.submission_state,
        submission_method=app.submission_method,
        submission_reference=app.submission_reference,
        submitted_at=app.submitted_at,
        acknowledged_at=app.acknowledged_at,
        follow_up_at=app.follow_up_at,
        resume_id=app.resume_id,
        resume_version_id=app.resume_version_id,
        created_at=app.created_at,
        created_by=app.created_by,
        job=to_card(app.job, m, None, app),
        allowed_next=sorted(
            s
            for s in allowed_next(app.status)
            if (app.status, s)
            not in {("awaiting_approval", "approved"), ("approved", "submitted"), ("failed", "approved")}
        ),
        pending_review=sum(1 for a in app.answers if a.needs_review),
    )


def _approval_out(r: ApprovalRequest, app: Application | None = None) -> ApprovalOut:
    return ApprovalOut(
        id=r.id,
        application_id=r.application_id,
        status=r.status,
        requested_by=r.requested_by,
        created_at=r.created_at,
        decided_at=r.decided_at,
        decided_via=r.decided_via,
        decision_note=r.decision_note,
        scheduled_for=r.scheduled_for,
        job_title=app.job.title if app else None,
        company=app.job.company.name if app else None,
    )


def _detail(db: DB, app: Application) -> ApplicationDetail:
    approvals = db.scalars(
        select(ApprovalRequest)
        .where(ApprovalRequest.application_id == app.id)
        .order_by(ApprovalRequest.created_at.desc())
    ).all()
    interviews = db.scalars(
        select(Interview).where(Interview.application_id == app.id).order_by(Interview.scheduled_at)
    ).all()
    return ApplicationDetail(
        **_out(db, app).model_dump(),
        cover_letter=app.cover_letter,
        cover_letter_generated_by=app.cover_letter_generated_by,
        answers=[AnswerOut.model_validate(a, from_attributes=True) for a in app.answers],
        events=[
            EventOut.model_validate(e, from_attributes=True)
            for e in sorted(app.events, key=lambda e: e.created_at, reverse=True)
        ],
        approvals=[_approval_out(r) for r in approvals],
        interviews=[InterviewOut.model_validate(i, from_attributes=True) for i in interviews],
        readiness=svc.readiness(app),
    )


def _err(exc: svc.ApplicationError) -> HTTPException:
    return HTTPException(409 if "limit" in str(exc).lower() else 400, str(exc))


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(db: DB, p: CurrentPrincipal, status: str | None = None) -> list[ApplicationOut]:
    q = select(Application).where(Application.user_id == p.id).order_by(Application.status_changed_at.desc())
    if status:
        q = q.where(Application.status.in_([s for s in status.split(",") if s in STATUSES]))
    return [_out(db, a) for a in db.scalars(q)]


@router.get("/applications/pipeline")
def pipeline(db: DB, p: CurrentPrincipal) -> dict:
    return {"stages": list(ACTIVE_PIPELINE) + ["rejected", "withdrawn", "failed"], "statuses": list(STATUSES)}


class CreateApplication(BaseModel):
    job_id: str
    status: Literal["discovered", "shortlisted", "preparing"] = "shortlisted"


@router.post("/applications", response_model=ApplicationOut, status_code=201)
def create_application(
    body: CreateApplication, db: DB, p: Principal = Depends(require_scope("applications:write"))
) -> ApplicationOut:
    try:
        job = jobs_svc.assert_visible(db, p.user, body.job_id)
    except SourceError as exc:
        raise HTTPException(404, "Job not found.") from exc
    try:
        return _out(db, svc.create(db, p.user, job, status=body.status, actor=p.actor))
    except svc.ApplicationError as exc:
        raise _err(exc) from exc


@router.get("/applications/{app_id}", response_model=ApplicationDetail)
def get_application(app_id: str, db: DB, p: CurrentPrincipal) -> ApplicationDetail:
    return _detail(db, get_owned(db, Application, app_id, p.id))


class TransitionIn(BaseModel):
    to: str
    note: str | None = Field(default=None, max_length=2000)


@router.post("/applications/{app_id}/transition", response_model=ApplicationDetail)
def move(
    app_id: str, body: TransitionIn, db: DB, p: Principal = Depends(require_scope("applications:write"))
) -> ApplicationDetail:
    app = get_owned(db, Application, app_id, p.id)
    try:
        svc.transition(db, app, body.to, p.actor, body.note)
    except svc.ApplicationError as exc:
        raise _err(exc) from exc
    return _detail(db, app)


class PrepareIn(BaseModel):
    resume_id: str | None = None
    resume_version_id: str | None = None
    questions: list[str] = Field(default_factory=list, max_length=25)
    generate_cover_letter: bool = False


@router.post("/applications/{app_id}/prepare")
def prepare(app_id: str, body: PrepareIn, db: DB, p: Principal = Depends(require_scope("applications:write"))) -> dict:
    app = get_owned(db, Application, app_id, p.id)
    try:
        result = svc.prepare(
            db,
            p.user,
            app,
            resume_id=body.resume_id,
            resume_version_id=body.resume_version_id,
            questions=body.questions,
            generate_cover_letter=body.generate_cover_letter,
            actor=p.actor,
        )
    except svc.ApplicationError as exc:
        raise _err(exc) from exc
    db.refresh(app)
    return {"application": _detail(db, app).model_dump(mode="json"), **result}


class AnswerIn(BaseModel):
    answer: str = Field(max_length=5000)


@router.put("/applications/{app_id}/answers/{answer_id}", response_model=AnswerOut)
def edit_answer(app_id: str, answer_id: str, body: AnswerIn, db: DB, p: Principal = Depends(web_only)) -> AnswerOut:
    app = get_owned(db, Application, app_id, p.id)
    try:
        return AnswerOut.model_validate(
            svc.update_answer(db, app, answer_id, body.answer, p.actor), from_attributes=True
        )
    except svc.ApplicationError as exc:
        raise _err(exc) from exc


class CoverLetterIn(BaseModel):
    cover_letter: str | None = Field(default=None, max_length=8000)


@router.put("/applications/{app_id}/cover-letter", status_code=204)
def edit_cover_letter(app_id: str, body: CoverLetterIn, db: DB, p: Principal = Depends(web_only)) -> None:
    svc.update_cover_letter(db, get_owned(db, Application, app_id, p.id), body.cover_letter, p.actor)


@router.post("/applications/{app_id}/request-approval", response_model=ApprovalOut)
def request_approval(app_id: str, db: DB, p: Principal = Depends(require_scope("applications:write"))) -> ApprovalOut:
    app = get_owned(db, Application, app_id, p.id)
    try:
        return _approval_out(svc.request_approval(db, p.user, app, p.actor), app)
    except svc.ApplicationError as exc:
        raise _err(exc) from exc


@router.get("/approvals", response_model=list[ApprovalOut])
def list_approvals(db: DB, p: CurrentPrincipal, status: str = "pending") -> list[ApprovalOut]:
    rows = db.scalars(
        select(ApprovalRequest)
        .where(ApprovalRequest.user_id == p.id, ApprovalRequest.status == status)
        .order_by(ApprovalRequest.created_at.desc())
    )
    return [_approval_out(r, db.get(Application, r.application_id)) for r in rows]


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=2000)
    scheduled_for: datetime | None = None


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalOut)
def decide(approval_id: str, body: DecisionIn, db: DB, p: Principal = Depends(web_only)) -> ApprovalOut:
    """Approve or reject. Human-only: requires a signed-in browser session with CSRF protection. API tokens
    (MCP clients, scripts, AI agents) are refused, whatever scopes they hold."""
    req = get_owned(db, ApprovalRequest, approval_id, p.id)
    try:
        r = svc.decide(
            db,
            p.user,
            req,
            approve=body.approve,
            via=p.via,
            actor=p.actor,
            note=body.note,
            scheduled_for=body.scheduled_for,
        )
    except svc.ApplicationError as exc:
        raise _err(exc) from exc
    return _approval_out(r, db.get(Application, r.application_id))


class SubmissionIn(BaseModel):
    action: Literal["start", "confirm", "fail", "unknown"]
    reference: str | None = Field(default=None, max_length=300)
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/applications/{app_id}/submission", response_model=ApplicationDetail)
def submission(app_id: str, body: SubmissionIn, db: DB, p: Principal = Depends(web_only)) -> ApplicationDetail:
    """Assisted submission is recorded by the person in the web app only — MCP clients can't submit."""
    app = get_owned(db, Application, app_id, p.id)
    try:
        svc.record_submission(db, p.user, app, body.action, p.actor, reference=body.reference, reason=body.reason)
    except svc.ApplicationError as exc:
        raise _err(exc) from exc
    return _detail(db, app)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/applications/{app_id}/notes", status_code=204)
def add_note(app_id: str, body: NoteIn, db: DB, p: Principal = Depends(require_scope("applications:write"))) -> None:
    svc.add_note(db, get_owned(db, Application, app_id, p.id), body.text, p.actor)


class FollowUpIn(BaseModel):
    follow_up_at: datetime | None


@router.put("/applications/{app_id}/follow-up", status_code=204)
def set_follow_up(app_id: str, body: FollowUpIn, db: DB, p: Principal = Depends(web_only)) -> None:
    app = get_owned(db, Application, app_id, p.id)
    app.follow_up_at = body.follow_up_at
    db.commit()


class InterviewIn(BaseModel):
    stage: str = Field(min_length=1, max_length=60)
    scheduled_at: datetime | None = None
    mode: str | None = Field(default=None, max_length=30)
    location_or_link: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/applications/{app_id}/interviews", response_model=InterviewOut, status_code=201)
def add_interview(app_id: str, body: InterviewIn, db: DB, p: Principal = Depends(web_only)) -> InterviewOut:
    app = get_owned(db, Application, app_id, p.id)
    try:
        iv = svc.schedule_interview(
            db,
            p.user,
            app,
            stage=body.stage,
            scheduled_at=body.scheduled_at,
            mode=body.mode,
            link=body.location_or_link,
            notes=body.notes,
            actor=p.actor,
        )
    except svc.ApplicationError as exc:
        raise _err(exc) from exc
    return InterviewOut.model_validate(iv, from_attributes=True)


@router.get("/applications/{app_id}/events", response_model=list[EventOut])
def events(app_id: str, db: DB, p: CurrentPrincipal) -> list[EventOut]:
    get_owned(db, Application, app_id, p.id)
    rows = db.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == app_id)
        .order_by(ApplicationEvent.created_at.desc())
    )
    return [EventOut.model_validate(e, from_attributes=True) for e in rows]
