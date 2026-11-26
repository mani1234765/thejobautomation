from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, true, update
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import fx
from app.domain import job_parsing as jp
from app.domain.matching import SCORING_VERSION, JobFacts, compute_match
from app.domain.salary_policy import SalaryFacts
from app.integrations.sources.base import NormalizedJob, SourceError
from app.integrations.sources.providers import get_adapter
from app.models import Application, Company, Job, JobMatch, JobSource, SavedJob, User
from app.services import audit
from app.services.profile import candidate_profile, salary_policy


@dataclass
class SyncResult:
    source_id: str
    status: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    closed: int = 0
    duplicates: int = 0
    error: str | None = None


def _company(db: Session, name: str) -> Company:
    norm = jp.normalize_company(name) or name.lower()
    c = db.scalar(select(Company).where(Company.normalized_name == norm))
    if c is None:
        c = Company(normalized_name=norm, name=name)
        db.add(c)
        db.flush()
    return c


def _apply(job: Job, n: NormalizedJob) -> None:
    for attr in (
        "title",
        "department",
        "location",
        "work_mode",
        "employment_type",
        "is_internship",
        "seniority",
        "experience_min",
        "experience_max",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "salary_disclosure",
        "salary_text",
        "description_text",
        "description_html",
        "required_skills",
        "preferred_skills",
        "url",
        "apply_url",
        "posted_at",
    ):
        setattr(job, attr, getattr(n, attr))
    job.normalized_title = jp.normalize_title(n.title)


def upsert_jobs(db: Session, source_key: str, company_name: str, jobs: list[NormalizedJob]) -> SyncResult:
    now = utcnow()
    res = SyncResult(source_id="", status="ok", fetched=len(jobs))
    company = _company(db, company_name)
    seen: set[str] = set()
    for n in jobs:
        if not n.title or not n.url:
            continue
        seen.add(n.external_id)
        fp = jp.fingerprint(company.name, n.title, n.location)
        job = db.scalar(select(Job).where(Job.provider == n.provider, Job.external_id == n.external_id))
        if job is None:
            job = Job(
                provider=n.provider,
                external_id=n.external_id,
                source_key=source_key,
                company_id=company.id,
                fingerprint=fp,
                first_seen_at=now,
                last_seen_at=now,
            )
            _apply(job, n)
            # Cross-source duplicate: same company + title + location seen from another board.
            original = db.scalar(
                select(Job.id).where(
                    Job.fingerprint == fp,
                    Job.provider != n.provider,
                    Job.duplicate_of_id.is_(None),
                    Job.closed_at.is_(None),
                )
            )
            if original:
                job.duplicate_of_id = original
                res.duplicates += 1
            db.add(job)
            res.created += 1
        else:
            _apply(job, n)
            job.fingerprint, job.last_seen_at, job.closed_at = fp, now, None
            res.updated += 1
    # Postings that disappeared from the board are closed (kept for history; applications reference them).
    stale = db.execute(
        update(Job)
        .where(Job.source_key == source_key, Job.closed_at.is_(None), Job.external_id.not_in(seen) if seen else true())
        .values(closed_at=now)
        .execution_options(synchronize_session=False)
    )
    res.closed = stale.rowcount or 0
    return res


def sync_source(db: Session, source: JobSource, *, actor: str = "user") -> SyncResult:
    try:
        company, jobs = get_adapter(source.provider).fetch(source.identifier)
    except SourceError as exc:
        source.last_synced_at, source.last_status, source.last_error = utcnow(), "error", str(exc)
        audit.record(
            db,
            user_id=source.user_id,
            actor=actor,
            action="job_source.sync_failed",
            entity_type="job_source",
            entity_id=source.id,
            meta={"error": str(exc)},
        )
        db.commit()
        return SyncResult(source_id=source.id, status="error", error=str(exc))
    res = upsert_jobs(db, source.source_key, company, jobs)
    res.source_id = source.id
    if source.display_name in (source.identifier, ""):
        source.display_name = company
    source.last_synced_at, source.last_status, source.last_error = utcnow(), "ok", None
    source.last_job_count = len(jobs)
    audit.record(
        db,
        user_id=source.user_id,
        actor=actor,
        action="job_source.synced",
        entity_type="job_source",
        entity_id=source.id,
        meta={"fetched": res.fetched, "created": res.created, "closed": res.closed},
    )
    db.commit()
    return res


# ── Matching ───────────────────────────────────────────────────────────────────────────────────
def job_facts(job: Job) -> JobFacts:
    return JobFacts(
        title=job.title,
        required_skills=job.required_skills or [],
        preferred_skills=job.preferred_skills or [],
        experience_min=job.experience_min,
        experience_max=job.experience_max,
        location=job.location,
        work_mode=job.work_mode,
        seniority=job.seniority,
        employment_type=job.employment_type,
        is_internship=job.is_internship,
        salary=SalaryFacts(
            is_internship=job.is_internship,
            disclosure=job.salary_disclosure,
            minimum=job.salary_min,
            maximum=job.salary_max,
            currency=job.salary_currency,
            period=job.salary_period,
            text=job.salary_text,
        ),
        description=job.description_text or "",
    )


def _source_keys(db: Session, user_id: str) -> list[str]:
    rows = db.execute(
        select(JobSource.provider, JobSource.identifier).where(
            JobSource.user_id == user_id, JobSource.enabled.is_(True)
        )
    )
    return [f"{p}:{i}" for p, i in rows]


def visible_jobs_clause(db: Session, user_id: str) -> Any:
    """Jobs a user can see: open postings from their enabled sources, plus jobs they've already acted on."""
    keys = _source_keys(db, user_id)
    acted = (
        select(Application.job_id)
        .where(Application.user_id == user_id)
        .union(select(SavedJob.job_id).where(SavedJob.user_id == user_id))
    )
    return or_(
        and_(Job.source_key.in_(keys), Job.closed_at.is_(None), Job.duplicate_of_id.is_(None)), Job.id.in_(acted)
    )


def policy_snapshot(user: User, fxc: fx.FxContext | None = None) -> dict[str, Any]:
    """The policy a decision was made under; stored with every decision."""
    p = salary_policy(user)
    snap: dict[str, Any] = {
        "min_ctc_lpa": p.min_ctc_lpa,
        "allow_undisclosed_salary": p.allow_undisclosed_salary,
        "min_stipend_monthly_inr": p.min_stipend_monthly_inr,
        "allow_undisclosed_stipend": p.allow_undisclosed_stipend,
        "engine": SCORING_VERSION,
    }
    table = fxc.table if fxc else None
    snap["fx"] = table.meta() if table else None
    return snap


def _policy_key(snap: dict[str, Any] | None) -> str | None:
    """Identity of the rules (not the exchange-rate snapshot, which changes daily)."""
    if not snap:
        return None
    core = {k: v for k, v in snap.items() if k != "fx"}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


def is_outdated(user: User, match: JobMatch | None) -> bool:
    return bool(match) and _policy_key(match.eligibility.get("policy")) != _policy_key(policy_snapshot(user))


def _acted_job_ids(db: Session, user_id: str) -> set[str]:
    return set(db.scalars(select(Application.job_id).where(Application.user_id == user_id))) | set(
        db.scalars(select(SavedJob.job_id).where(SavedJob.user_id == user_id))
    )


def ensure_matches(db: Session, user: User, job_ids: list[str] | None = None, *, force: bool = False) -> int:
    """(Re)compute match rows whose inputs changed. Deterministic, so caching by input hash is safe.

    Jobs the user has saved or applied to keep the decision (and policy snapshot) they were evaluated
    under when the *policy* changes, e.g. a new salary threshold. Such a kept decision is not recomputed
    for any reason, including later profile corrections, until the user re-evaluates it (`force`), so
    history isn't silently rewritten. While the policy is unchanged, profile corrections do refresh
    decisions for every job, saved and applied ones included.
    """
    cand = candidate_profile(db, user)
    fxc = fx.current_context()
    snap = policy_snapshot(user, fxc)
    pkey = _policy_key(snap)
    digest = hashlib.sha256(f"{cand.digest()}|{pkey}".encode()).hexdigest()
    policy = salary_policy(user)
    q = select(Job).where(visible_jobs_clause(db, user.id))
    if job_ids is not None:
        q = select(Job).where(Job.id.in_(job_ids))
    existing = {m.job_id: m for m in db.scalars(select(JobMatch).where(JobMatch.user_id == user.id))}
    acted = _acted_job_ids(db, user.id)
    now, n = utcnow(), 0
    for job in db.scalars(q):
        m = existing.get(job.id)
        if m and not force:
            if m.profile_hash == digest and m.scoring_version == SCORING_VERSION and m.computed_at >= job.updated_at:
                continue
            if job.id in acted and _policy_key(m.eligibility.get("policy")) != pkey:
                continue  # historical decision under an earlier policy: re-evaluated only on request
        r = compute_match(job_facts(job), cand, policy, fxc)
        data = r.as_dict()
        eligibility = data.pop("eligibility")
        eligibility["policy"], eligibility["evaluated_at"] = snap, now.isoformat()
        if m is None:
            m = JobMatch(user_id=user.id, job_id=job.id)
            db.add(m)
        m.score, m.breakdown, m.eligibility = r.score, data, eligibility
        m.scoring_version, m.profile_hash, m.computed_at = SCORING_VERSION, digest, now
        n += 1
    db.commit()
    return n


def reevaluate(db: Session, user: User, job_ids: list[str] | None = None, *, actor: str) -> int:
    """Explicitly re-evaluate decisions kept from an earlier policy (all outdated ones by default)."""
    if job_ids is None:
        matches = db.scalars(select(JobMatch).where(JobMatch.user_id == user.id))
        job_ids = [m.job_id for m in matches if is_outdated(user, m)]
    if not job_ids:
        return 0
    n = ensure_matches(db, user, job_ids, force=True)
    audit.record(
        db,
        user_id=user.id,
        actor=actor,
        action="eligibility.reevaluated",
        meta={"jobs": len(job_ids), "policy": policy_snapshot(user)},
    )
    db.commit()
    return n


# ── Search ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class JobQuery:
    q: str | None = None
    company: str | None = None
    location: str | None = None
    work_modes: list[str] | None = None
    employment_types: list[str] | None = None
    experience_levels: list[str] | None = None
    min_salary_lpa: float | None = None
    skills: list[str] | None = None
    posted_within_days: int | None = None
    min_score: int | None = None
    eligibility: str | None = None
    state: str = "active"  # active | saved | dismissed | all
    sort: str = "match"  # match | recent
    page: int = 1
    page_size: int = 25


_LEVEL_EXP = {
    "fresher": (0, 0),
    "internship": (0, 1),
    "0-1": (0, 1),
    "1-2": (1, 2),
    "2-3": (2, 3),
    "3-5": (3, 5),
    "5+": (5, 99),
}


def search(db: Session, user: User, query: JobQuery) -> tuple[list[tuple[Job, JobMatch | None, str | None]], int]:
    ensure_matches(db, user)
    saved_state = (
        select(SavedJob.state)
        .where(SavedJob.user_id == user.id, SavedJob.job_id == Job.id)
        .correlate(Job)
        .scalar_subquery()
    )
    stmt = (
        select(Job, JobMatch, saved_state.label("saved_state"))
        .outerjoin(JobMatch, and_(JobMatch.job_id == Job.id, JobMatch.user_id == user.id))
        .join(Company, Company.id == Job.company_id)
        .where(visible_jobs_clause(db, user.id))
    )
    if query.q:
        like = f"%{query.q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Job.title).like(like),
                Company.normalized_name.like(like),
                func.lower(Job.department).like(like),
            )
        )
    if query.company:
        stmt = stmt.where(Company.normalized_name.like(f"%{jp.normalize_company(query.company)}%"))
    if query.location:
        stmt = stmt.where(func.lower(Job.location).like(f"%{query.location.strip().lower()}%"))
    if query.work_modes:
        stmt = stmt.where(Job.work_mode.in_(query.work_modes))
    if query.employment_types:
        conds = [Job.employment_type.in_(query.employment_types)]
        if "internship" in query.employment_types:
            conds.append(Job.is_internship.is_(True))
        stmt = stmt.where(or_(*conds))
    if query.experience_levels:
        conds = []
        for lv in query.experience_levels:
            lo, hi = _LEVEL_EXP.get(lv, (0, 99))
            if lv == "internship":
                conds.append(Job.is_internship.is_(True))
            conds.append(
                and_(
                    Job.experience_min.is_not(None),
                    Job.experience_min <= hi,
                    func.coalesce(Job.experience_max, Job.experience_min + 2) >= lo,
                )
            )
        stmt = stmt.where(or_(*conds))
    if query.min_salary_lpa is not None:
        # Undisclosed pay stays visible (it's eligible by default policy); disclosed INR pay must clear the bar.
        stmt = stmt.where(
            or_(
                Job.salary_max.is_(None), Job.salary_currency != "INR", Job.salary_max >= query.min_salary_lpa * 100_000
            )
        )
    if query.posted_within_days:
        stmt = stmt.where(Job.posted_at >= utcnow() - timedelta(days=query.posted_within_days))
    if query.min_score is not None:
        stmt = stmt.where(JobMatch.score >= query.min_score)
    if query.state == "saved":
        stmt = stmt.where(saved_state == "saved")
    elif query.state == "dismissed":
        stmt = stmt.where(saved_state == "dismissed")
    elif query.state == "active":
        stmt = stmt.where(or_(saved_state.is_(None), saved_state != "dismissed"))

    rows = list(db.execute(stmt).all())
    if query.skills:
        want = {s.lower() for s in query.skills}
        rows = [
            r for r in rows if want & {s.lower() for s in (r[0].required_skills or []) + (r[0].preferred_skills or [])}
        ]
    if query.eligibility:
        rows = [r for r in rows if r[1] and r[1].eligibility.get("status") == query.eligibility]
    if query.sort == "recent":
        rows.sort(key=lambda r: r[0].posted_at or r[0].first_seen_at, reverse=True)
    else:
        rows.sort(key=lambda r: ((r[1].score if r[1] else -1), r[0].posted_at or r[0].first_seen_at), reverse=True)
    total = len(rows)
    start = (query.page - 1) * query.page_size
    return [(r[0], r[1], r[2]) for r in rows[start : start + query.page_size]], total


def set_saved_state(db: Session, user: User, job: Job, state: str | None, *, actor: str) -> None:
    row = db.scalar(select(SavedJob).where(SavedJob.user_id == user.id, SavedJob.job_id == job.id))
    if state is None:
        if row:
            db.delete(row)
    elif row is None:
        db.add(SavedJob(user_id=user.id, job_id=job.id, state=state))
    else:
        row.state = state
    audit.record(
        db, user_id=user.id, actor=actor, action=f"job.{state or 'unsaved'}", entity_type="job", entity_id=job.id
    )
    db.commit()


def assert_visible(db: Session, user: User, job_id: str) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id, visible_jobs_clause(db, user.id)))
    if job is None:
        # Closed postings and duplicates are reachable if the user follows the source.
        job = db.get(Job, job_id)
        if job is None or job.source_key not in _source_keys(db, user.id):
            raise SourceError("Job not found.")
    return job
