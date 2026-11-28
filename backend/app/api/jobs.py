from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, require_scope
from app.domain import job_filters as jf
from app.integrations.sources.base import SourceError
from app.models import Application, Job, JobMatch, JobSource, SavedJob
from app.services import audit
from app.services import jobs as svc

router = APIRouter(tags=["jobs"])


class JobCard(BaseModel):
    id: str
    title: str
    company: str
    location: str | None
    work_mode: str | None
    employment_type: str | None
    is_internship: bool
    seniority: str | None
    experience_min: float | None
    experience_max: float | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    salary_disclosure: str
    department: str | None
    provider: str
    url: str
    apply_url: str | None
    posted_at: datetime | None
    first_seen_at: datetime
    closed: bool
    required_skills: list[str]
    preferred_skills: list[str]
    match_score: int | None
    eligibility: str | None
    matched_skills: list[str]
    missing_skills: list[str]
    saved_state: str | None
    application_id: str | None = None
    application_status: str | None = None


class JobDetail(JobCard):
    description_html: str | None
    description_text: str
    match: dict[str, Any] | None
    eligibility_detail: dict[str, Any] | None
    # True when the stored decision no longer reflects its inputs; the reasons say which changed
    # (policy, engine, profile, listing, untracked). Saved/applied jobs keep it until re-evaluated.
    eligibility_outdated: bool = False
    eligibility_outdated_reasons: list[str] = Field(default_factory=list)
    # With reason "policy": which settings changed since the decision, each {key, group, before, after}.
    eligibility_policy_changes: list[dict[str, Any]] = Field(default_factory=list)
    duplicate_of_id: str | None
    # M3 extraction. mentioned_skills is None for jobs not re-synced since M3 (unknown, not "none").
    mentioned_skills: list[str] | None = None
    experience_preferred_min: float | None = None
    remote_region: str | None = None  # informational only; never used for eligibility
    field_evidence: dict[str, Any] = Field(default_factory=dict)


def to_card(job: Job, match: JobMatch | None, saved: str | None, app: Application | None = None) -> JobCard:
    b = match.breakdown if match else {}
    return JobCard(
        id=job.id,
        title=job.title,
        company=job.company.name,
        location=job.location,
        work_mode=job.work_mode,
        employment_type=job.employment_type,
        is_internship=job.is_internship,
        seniority=job.seniority,
        experience_min=job.experience_min,
        experience_max=job.experience_max,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        salary_period=job.salary_period,
        salary_disclosure=job.salary_disclosure,
        department=job.department,
        provider=job.provider,
        url=job.url,
        apply_url=job.apply_url,
        posted_at=job.posted_at,
        first_seen_at=job.first_seen_at,
        closed=job.closed_at is not None,
        required_skills=job.required_skills or [],
        preferred_skills=job.preferred_skills or [],
        match_score=match.score if match else None,
        eligibility=match.eligibility.get("status") if match else None,
        matched_skills=b.get("matched_skills", []),
        missing_skills=b.get("missing_required", []),
        saved_state=saved,
        application_id=app.id if app else None,
        application_status=app.status if app else None,
    )


class JobPage(BaseModel):
    items: list[JobCard]
    total: int
    page: int
    page_size: int
    sources: int


def _csv(v: str | None) -> list[str] | None:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def _choice_csv(name: str, v: str | None) -> list[str] | None:
    """A comma-separated multiple-choice filter; unknown values are a 422 naming the allowed ones."""
    try:
        return jf.choices(name, _csv(v)) or None
    except jf.InvalidFilter as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/jobs", response_model=JobPage)
def search_jobs(
    db: DB,
    p: CurrentPrincipal,
    q: str | None = None,
    company: str | None = None,
    location: str | None = None,
    work_modes: str | None = None,
    employment_types: str | None = None,
    experience_levels: str | None = None,
    min_salary_lpa: Annotated[float | None, Query(ge=0)] = None,
    skills: str | None = None,
    posted_within_days: Annotated[int | None, Query(ge=1, le=365)] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    eligibility: Literal["eligible", "review", "ineligible"] | None = None,
    state: Literal["active", "saved", "dismissed", "all"] = "active",
    sort: Literal["match", "recent"] = "match",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> JobPage:
    query = svc.JobQuery(
        q=q,
        company=company,
        location=location,
        work_modes=_choice_csv("work_modes", work_modes),
        employment_types=_choice_csv("employment_types", employment_types),
        experience_levels=_choice_csv("experience_levels", experience_levels),
        min_salary_lpa=min_salary_lpa,
        skills=_csv(skills),
        posted_within_days=posted_within_days,
        min_score=min_score,
        eligibility=eligibility,
        state=state,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    rows, total = svc.search(db, p.user, query)
    apps = {
        a.job_id: a
        for a in db.scalars(
            select(Application).where(Application.user_id == p.id, Application.job_id.in_([j.id for j, _, _ in rows]))
        )
    }
    n_sources = len(svc._source_keys(db, p.id))
    return JobPage(
        items=[to_card(j, m, s, apps.get(j.id)) for j, m, s in rows],
        total=total,
        page=page,
        page_size=page_size,
        sources=n_sources,
    )


def _detail(db: DB, p: Principal, job: Job) -> JobDetail:
    svc.ensure_matches(db, p.user, [job.id])
    m = db.scalar(select(JobMatch).where(JobMatch.user_id == p.id, JobMatch.job_id == job.id))
    saved = db.scalar(select(SavedJob.state).where(SavedJob.user_id == p.id, SavedJob.job_id == job.id))
    app = db.scalar(select(Application).where(Application.user_id == p.id, Application.job_id == job.id))
    card = to_card(job, m, saved, app)
    return JobDetail(
        **card.model_dump(),
        description_html=job.description_html,
        description_text=job.description_text,
        match=m.breakdown if m else None,
        eligibility_detail=m.eligibility if m else None,
        eligibility_outdated=bool(reasons := svc.outdated_reasons(db, p.user, job, m)),
        eligibility_outdated_reasons=reasons,
        eligibility_policy_changes=svc.policy_changes(p.user, m) if "policy" in reasons else [],
        duplicate_of_id=job.duplicate_of_id,
        mentioned_skills=job.mentioned_skills,
        experience_preferred_min=job.experience_preferred_min,
        remote_region=job.remote_region,
        field_evidence=job.field_evidence or {},
    )


def _visible(db: DB, p: Principal, job_id: str) -> Job:
    try:
        return svc.assert_visible(db, p.user, job_id)
    except SourceError as exc:
        raise HTTPException(404, "Job not found.") from exc


@router.get("/jobs/compare", response_model=list[JobDetail])
def compare_jobs(ids: str, db: DB, p: CurrentPrincipal) -> list[JobDetail]:
    job_ids = _csv(ids) or []
    if not 2 <= len(job_ids) <= 4:
        raise HTTPException(400, "Compare between two and four jobs.")
    return [_detail(db, p, _visible(db, p, i)) for i in job_ids]


@router.get("/jobs/{job_id}", response_model=JobDetail)
def job_detail(job_id: str, db: DB, p: CurrentPrincipal) -> JobDetail:
    return _detail(db, p, _visible(db, p, job_id))


class ReevaluateOut(BaseModel):
    reevaluated: int


@router.post("/jobs/reevaluate", response_model=ReevaluateOut)
def reevaluate_all(db: DB, p: Principal = Depends(require_scope("jobs:write"))) -> ReevaluateOut:
    """Re-evaluate every decision that was made under an earlier policy."""
    return ReevaluateOut(reevaluated=svc.reevaluate(db, p.user, actor=p.actor))


@router.post("/jobs/{job_id}/reevaluate", response_model=JobDetail)
def reevaluate_job(job_id: str, db: DB, p: Principal = Depends(require_scope("jobs:write"))) -> JobDetail:
    job = _visible(db, p, job_id)
    svc.reevaluate(db, p.user, [job.id], actor=p.actor)
    return _detail(db, p, job)


class SaveState(BaseModel):
    state: Literal["saved", "dismissed"] | None


@router.put("/jobs/{job_id}/state", status_code=204)
def set_job_state(job_id: str, body: SaveState, db: DB, p: Principal = Depends(require_scope("jobs:write"))) -> None:
    svc.set_saved_state(db, p.user, _visible(db, p, job_id), body.state, actor=p.actor)


# ── Sources ────────────────────────────────────────────────────────────────────────────────────
class SourceIn(BaseModel):
    provider: Literal["greenhouse", "lever", "ashby"]
    identifier: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("identifier")
    @classmethod
    def _ident(cls, v: str) -> str:
        v = v.strip()
        # Accept pasted board URLs: boards.greenhouse.io/stripe, jobs.lever.co/palantir, jobs.ashbyhq.com/ramp
        m = re.search(r"(?:greenhouse\.io|lever\.co|ashbyhq\.com)/(?:embed/job_board\?for=)?([A-Za-z0-9._-]+)", v)
        if m:
            v = m.group(1)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", v):
            raise ValueError("Use the board identifier (e.g. 'stripe') or paste the board URL.")
        return v


class SourceOut(BaseModel):
    id: str
    provider: str
    identifier: str
    display_name: str
    enabled: bool
    last_synced_at: datetime | None
    last_status: str | None
    last_error: str | None
    last_job_count: int | None


def _source_out(s: JobSource) -> SourceOut:
    return SourceOut(
        id=s.id,
        provider=s.provider,
        identifier=s.identifier,
        display_name=s.display_name,
        enabled=s.enabled,
        last_synced_at=s.last_synced_at,
        last_status=s.last_status,
        last_error=s.last_error,
        last_job_count=s.last_job_count,
    )


@router.get("/job-sources", response_model=list[SourceOut])
def list_sources(db: DB, p: CurrentPrincipal) -> list[SourceOut]:
    return [
        _source_out(s)
        for s in db.scalars(select(JobSource).where(JobSource.user_id == p.id).order_by(JobSource.created_at))
    ]


@router.post("/job-sources", response_model=SourceOut, status_code=201)
def add_source(body: SourceIn, db: DB, p: Principal = Depends(require_scope("jobs:write"))) -> SourceOut:
    if db.scalar(
        select(JobSource).where(
            JobSource.user_id == p.id, JobSource.provider == body.provider, JobSource.identifier == body.identifier
        )
    ):
        raise HTTPException(409, "You already follow this board.")
    s = JobSource(
        user_id=p.id,
        provider=body.provider,
        identifier=body.identifier,
        display_name=body.display_name or body.identifier,
    )
    db.add(s)
    db.flush()
    audit.record(db, user_id=p.id, actor=p.actor, action="job_source.added", entity_type="job_source", entity_id=s.id)
    db.commit()
    # First sync happens immediately so the user sees real results (or a real error) right away.
    svc.sync_source(db, s, actor=p.actor)
    db.refresh(s)
    return _source_out(s)


@router.post("/job-sources/{source_id}/sync", response_model=SourceOut)
def sync_source(source_id: str, db: DB, p: Principal = Depends(require_scope("jobs:write"))) -> SourceOut:
    s = get_owned(db, JobSource, source_id, p.id)
    svc.sync_source(db, s, actor=p.actor)
    db.refresh(s)
    return _source_out(s)


class SourcePatch(BaseModel):
    enabled: bool


@router.patch("/job-sources/{source_id}", response_model=SourceOut)
def toggle_source(
    source_id: str, body: SourcePatch, db: DB, p: Principal = Depends(require_scope("jobs:write"))
) -> SourceOut:
    s = get_owned(db, JobSource, source_id, p.id)
    s.enabled = body.enabled
    db.commit()
    return _source_out(s)


@router.delete("/job-sources/{source_id}", status_code=204)
def delete_source(source_id: str, db: DB, p: Principal = Depends(require_scope("jobs:write"))) -> None:
    s = get_owned(db, JobSource, source_id, p.id)
    audit.record(
        db,
        user_id=p.id,
        actor=p.actor,
        action="job_source.removed",
        entity_type="job_source",
        entity_id=s.id,
        meta={"source": s.source_key},
    )
    db.delete(s)
    db.commit()
