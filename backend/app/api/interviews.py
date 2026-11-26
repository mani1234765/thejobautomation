from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, web_only
from app.integrations.sources.base import SourceError
from app.models import Application, Interview, InterviewSession
from app.services import interviews as svc
from app.services import jobs as jobs_svc

router = APIRouter(prefix="/interview", tags=["interview"])


class SessionOut(BaseModel):
    id: str
    title: str
    job_id: str | None
    application_id: str | None
    generated_by: str
    status: str
    created_at: datetime
    progress: dict[str, int]
    focus_areas: list[str]
    questions: list[dict[str, Any]]


def _out(s: InterviewSession) -> SessionOut:
    meta = next((q for q in s.questions if q.get("id") == "_meta"), {})
    return SessionOut(
        id=s.id,
        title=s.title,
        job_id=s.job_id,
        application_id=s.application_id,
        generated_by=s.generated_by,
        status=s.status,
        created_at=s.created_at,
        progress=svc.progress(s),
        focus_areas=meta.get("focus_areas", []),
        questions=[q for q in s.questions if q.get("id") != "_meta"],
    )


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: DB, p: CurrentPrincipal) -> list[SessionOut]:
    return [
        _out(s)
        for s in db.scalars(
            select(InterviewSession)
            .where(InterviewSession.user_id == p.id)
            .order_by(InterviewSession.created_at.desc())
        )
    ]


class CreateSession(BaseModel):
    job_id: str | None = None
    application_id: str | None = None


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session(body: CreateSession, db: DB, p: Principal = Depends(web_only)) -> SessionOut:
    job = None
    if body.application_id:
        app = get_owned(db, Application, body.application_id, p.id)
        job = app.job
    elif body.job_id:
        try:
            job = jobs_svc.assert_visible(db, p.user, body.job_id)
        except SourceError as exc:
            raise HTTPException(404, "Job not found.") from exc
    return _out(svc.create_session(db, p.user, job, body.application_id))


@router.get("/sessions/{sid}", response_model=SessionOut)
def get_session(sid: str, db: DB, p: CurrentPrincipal) -> SessionOut:
    return _out(get_owned(db, InterviewSession, sid, p.id))


class AnswerIn(BaseModel):
    question_id: str
    answer: str = Field(min_length=1, max_length=6000)


@router.post("/sessions/{sid}/answer")
def answer(sid: str, body: AnswerIn, db: DB, p: Principal = Depends(web_only)) -> dict:
    s = get_owned(db, InterviewSession, sid, p.id)
    try:
        return svc.evaluate_answer(db, p.user, s, body.question_id, body.answer)
    except KeyError as exc:
        raise HTTPException(404, "Question not found.") from exc


@router.delete("/sessions/{sid}", status_code=204)
def delete_session(sid: str, db: DB, p: Principal = Depends(web_only)) -> None:
    db.delete(get_owned(db, InterviewSession, sid, p.id))
    db.commit()


@router.get("/upcoming")
def upcoming(db: DB, p: CurrentPrincipal) -> list[dict]:
    rows = db.scalars(select(Interview).where(Interview.user_id == p.id).order_by(Interview.scheduled_at))
    out = []
    for iv in rows:
        app = db.get(Application, iv.application_id)
        out.append(
            {
                "id": iv.id,
                "stage": iv.stage,
                "scheduled_at": iv.scheduled_at,
                "mode": iv.mode,
                "location_or_link": iv.location_or_link,
                "application_id": iv.application_id,
                "job_title": app.job.title if app else None,
                "company": app.job.company.name if app else None,
            }
        )
    return out
