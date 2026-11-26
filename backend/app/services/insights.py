"""Dashboard and analytics. Every number is computed from stored records; metric definitions are
returned alongside values so the UI can explain them, and unavailable data is reported as `null`
with a reason rather than as zero."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.application_state import STATUSES
from app.models import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    ApprovalRequest,
    Communication,
    Interview,
    Job,
    JobMatch,
    JobSource,
    Resume,
    User,
)
from app.services import jobs as jobs_svc
from app.services.assistant import skill_demand

RESPONSE_CATEGORIES = ("interview_invitation", "rejection", "offer", "assessment", "recruiter_outreach")
REACHED_INTERVIEW = {"interview", "offer"}


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def dashboard(db: Session, user: User) -> dict[str, Any]:
    uid = user.id
    jobs_svc.ensure_matches(db, user)
    visible = jobs_svc.visible_jobs_clause(db, uid)
    threshold = user.settings.min_match_score
    n_jobs = db.scalar(select(func.count()).select_from(Job).where(visible)) or 0
    matches = db.execute(
        select(Job, JobMatch)
        .join(JobMatch, (JobMatch.job_id == Job.id) & (JobMatch.user_id == uid))
        .where(visible, Job.closed_at.is_(None), JobMatch.score >= threshold)
        .order_by(JobMatch.score.desc())
    ).all()
    matching = [(j, m) for j, m in matches if m.eligibility.get("status") != "ineligible"]
    apps = db.scalars(select(Application).where(Application.user_id == uid)).all()
    by_status = Counter(a.status for a in apps)
    now = utcnow()
    upcoming_interviews = db.scalars(
        select(Interview)
        .where(Interview.user_id == uid, Interview.scheduled_at >= now)
        .order_by(Interview.scheduled_at)
        .limit(5)
    ).all()
    pending_approvals = db.scalars(
        select(ApprovalRequest).where(ApprovalRequest.user_id == uid, ApprovalRequest.status == "pending")
    ).all()
    review_answers = (
        db.scalar(
            select(func.count())
            .select_from(ApplicationAnswer)
            .join(Application)
            .where(
                Application.user_id == uid, ApplicationAnswer.needs_review.is_(True), Application.status == "preparing"
            )
        )
        or 0
    )
    followups = [
        a
        for a in apps
        if a.follow_up_at and _as_aware(a.follow_up_at) <= now + timedelta(days=1) and a.status == "submitted"
    ]
    responses = (
        db.scalar(
            select(func.count())
            .select_from(Communication)
            .where(
                Communication.user_id == uid,
                Communication.direction == "inbound",
                Communication.category.in_(RESPONSE_CATEGORIES),
            )
        )
        or 0
    )
    sources = db.scalar(select(func.count()).select_from(JobSource).where(JobSource.user_id == uid)) or 0

    # 30-day activity series
    since = now - timedelta(days=29)
    days = [(since + timedelta(days=i)).date() for i in range(30)]
    discovered = Counter(
        _as_aware(d).date() for (d,) in db.execute(select(Job.first_seen_at).where(visible, Job.first_seen_at >= since))
    )
    created = Counter(_as_aware(a.created_at).date() for a in apps if _as_aware(a.created_at) >= since)
    submitted = Counter(
        _as_aware(a.submitted_at).date() for a in apps if a.submitted_at and _as_aware(a.submitted_at) >= since
    )
    activity = [
        {
            "date": d.isoformat(),
            "discovered": discovered.get(d, 0),
            "applications": created.get(d, 0),
            "submitted": submitted.get(d, 0),
        }
        for d in days
    ]

    demand = skill_demand(db, user, limit=10)
    companies = Counter(j.company.name for j, _ in matching).most_common(6)
    primary = db.scalar(select(Resume).where(Resume.user_id == uid, Resume.is_primary.is_(True)))

    events = db.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.user_id == uid)
        .order_by(ApplicationEvent.created_at.desc())
        .limit(12)
    ).all()
    app_by_id = {a.id: a for a in apps}
    timeline = []
    for e in events:
        a = app_by_id.get(e.application_id)
        if a:
            timeline.append(
                {
                    "id": e.id,
                    "kind": e.kind,
                    "to_status": e.to_status,
                    "from_status": e.from_status,
                    "actor": e.actor,
                    "detail": e.detail,
                    "at": e.created_at,
                    "application_id": a.id,
                    "job_title": a.job.title,
                    "company": a.job.company.name,
                }
            )

    return {
        "metrics": {
            "jobs_discovered": n_jobs,
            "matching_jobs": len(matching),
            "match_threshold": threshold,
            "applications_submitted": sum(1 for a in apps if a.submission_state == "acknowledged"),
            "awaiting_approval": len(pending_approvals),
            "interviews_scheduled": len(upcoming_interviews),
            "recruiter_responses": responses,
            "rejections": by_status.get("rejected", 0),
            "offers": by_status.get("offer", 0),
            "pending_actions": len(pending_approvals) + review_answers + len(followups),
            "sources": sources,
        },
        "pipeline": [{"status": s, "count": by_status.get(s, 0)} for s in STATUSES],
        "activity": activity,
        "recommended": [
            {
                "job_id": j.id,
                "title": j.title,
                "company": j.company.name,
                "location": j.location,
                "work_mode": j.work_mode,
                "score": m.score,
                "eligibility": m.eligibility.get("status"),
                "matched_skills": m.breakdown.get("matched_skills", [])[:4],
                "applied": any(a.job_id == j.id for a in apps),
            }
            for j, m in matching[:6]
        ],
        "insights": {
            "skill_demand": demand,
            "companies_hiring": [{"company": c, "matching_jobs": n} for c, n in companies],
            "resume": {
                "id": primary.id,
                "name": primary.name,
                "score": (primary.quality or {}).get("score"),
                "top_issues": (primary.quality or {}).get("issues", [])[:3],
            }
            if primary
            else None,
            "next_actions": next_actions(
                user, sources, primary, len(pending_approvals), review_answers, followups, matching, apps, demand
            ),
        },
        "timeline": timeline,
        "upcoming": {
            "interviews": [
                {
                    "id": i.id,
                    "stage": i.stage,
                    "scheduled_at": i.scheduled_at,
                    "application_id": i.application_id,
                    "company": app_by_id[i.application_id].job.company.name if i.application_id in app_by_id else None,
                }
                for i in upcoming_interviews
            ],
            "follow_ups": [
                {"application_id": a.id, "company": a.job.company.name, "title": a.job.title, "due": a.follow_up_at}
                for a in followups[:5]
            ],
            "approvals": [
                {
                    "id": r.id,
                    "application_id": r.application_id,
                    "title": app_by_id[r.application_id].job.title if r.application_id in app_by_id else None,
                    "company": app_by_id[r.application_id].job.company.name if r.application_id in app_by_id else None,
                    "created_at": r.created_at,
                }
                for r in pending_approvals[:5]
            ],
        },
    }


def next_actions(
    user: User,
    sources: int,
    primary: Resume | None,
    approvals: int,
    review_answers: int,
    followups: list,
    matching: list,
    apps: list,
    demand: dict,
) -> list[dict[str, str]]:
    acts: list[dict[str, str]] = []
    if not primary:
        acts.append({"title": "Upload your resume", "detail": "It powers matching and tailoring.", "href": "/resume"})
    elif user.profile.verified_at is None:
        acts.append(
            {
                "title": "Verify your profile",
                "detail": "Imported details stay unverified until you confirm them.",
                "href": "/settings/profile",
            }
        )
    if sources == 0:
        acts.append(
            {
                "title": "Follow company job boards",
                "detail": "Add Greenhouse, Lever or Ashby boards to start discovering jobs.",
                "href": "/jobs?sources=1",
            }
        )
    if approvals:
        acts.append(
            {
                "title": f"Review {approvals} approval request{'s' if approvals > 1 else ''}",
                "detail": "Nothing is submitted until you approve.",
                "href": "/applications?view=approvals",
            }
        )
    if review_answers:
        acts.append(
            {
                "title": f"Answer {review_answers} open application question{'s' if review_answers > 1 else ''}",
                "detail": "JobPilot won't guess missing information.",
                "href": "/applications",
            }
        )
    if followups:
        acts.append(
            {
                "title": f"Follow up on {len(followups)} application{'s' if len(followups) > 1 else ''}",
                "detail": "It's been a week since submission.",
                "href": "/communications",
            }
        )
    applied = {a.job_id for a in apps}
    fresh = [j for j, m in matching if j.id not in applied]
    if fresh:
        acts.append(
            {
                "title": f"Shortlist from {len(fresh)} strong matches",
                "detail": f"Top: {fresh[0].title} at {fresh[0].company.name}",
                "href": "/jobs",
            }
        )
    if demand.get("gaps"):
        gap = demand["gaps"][0]
        n = demand.get("gap_counts", {}).get(gap, 0)
        acts.append(
            {
                "title": f"Skill gap: {gap}",
                "detail": f"Asked for in {n} of your {demand.get('matching_jobs_analysed', 0)} matching jobs.",
                "href": "/analytics",
            }
        )
    return acts[:5]


def analytics(db: Session, user: User, start: datetime, end: datetime) -> dict[str, Any]:
    uid = user.id
    visible = jobs_svc.visible_jobs_clause(db, uid)
    jobs = db.scalars(select(Job).where(visible, Job.first_seen_at >= start, Job.first_seen_at <= end)).all()
    apps = db.scalars(select(Application).where(Application.user_id == uid)).all()
    in_range = [a for a in apps if start <= _as_aware(a.created_at) <= end]
    submitted = [a for a in in_range if a.submission_state == "acknowledged"]
    span = max(1, (end - start).days)
    bucket = "week" if span > 45 else "day"

    def key(dt: datetime) -> str:
        d = _as_aware(dt).date()
        return (d - timedelta(days=d.weekday())).isoformat() if bucket == "week" else d.isoformat()

    series: dict[str, dict[str, int]] = defaultdict(lambda: {"discovered": 0, "created": 0, "submitted": 0})
    cursor = start
    while cursor <= end:
        series[key(cursor)]
        cursor += timedelta(days=7 if bucket == "week" else 1)
    for j in jobs:
        series[key(j.first_seen_at)]["discovered"] += 1
    for a in in_range:
        series[key(a.created_at)]["created"] += 1
        if a.submitted_at:
            series[key(a.submitted_at)]["submitted"] += 1

    events = db.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.user_id == uid, ApplicationEvent.kind == "status_change")
        .order_by(ApplicationEvent.application_id, ApplicationEvent.created_at)
    ).all()
    reached: dict[str, set[str]] = defaultdict(set)
    durations: dict[str, list[float]] = defaultdict(list)
    prev: dict[str, ApplicationEvent] = {}
    for e in events:
        if e.to_status:
            reached[e.application_id].add(e.to_status)
        p = prev.get(e.application_id)
        if p and p.to_status:
            durations[p.to_status].append((_as_aware(e.created_at) - _as_aware(p.created_at)).total_seconds() / 86400)
        prev[e.application_id] = e

    sub_ids = {a.id for a in submitted}
    interviewed = {a for a in sub_ids if reached[a] & REACHED_INTERVIEW}
    offered = {a for a in sub_ids if "offer" in reached[a]}
    responded_apps = {
        c.application_id
        for c in db.scalars(
            select(Communication).where(
                Communication.user_id == uid,
                Communication.direction == "inbound",
                Communication.category.in_(RESPONSE_CATEGORIES),
            )
        )
        if c.application_id
    }
    responded = {
        a for a in sub_ids if a in responded_apps or reached[a] & {"assessment", "interview", "offer", "rejected"}
    }

    def rate(num: int, den: int) -> float | None:
        return round(num / den, 3) if den else None

    skills: Counter[str] = Counter()
    for j in jobs:
        skills.update(set(j.required_skills or []) | set(j.preferred_skills or []))
    resumes = db.scalars(select(Resume).where(Resume.user_id == uid).order_by(Resume.created_at)).all()
    return {
        "range": {"start": start, "end": end, "bucket": bucket},
        "series": [{"period": k, **v} for k, v in sorted(series.items())],
        "totals": {
            "jobs_discovered": len(jobs),
            "applications": len(in_range),
            "submitted": len(submitted),
            "interviews": len(interviewed),
            "offers": len(offered),
        },
        "conversion": {
            "application_to_interview": rate(len(interviewed), len(submitted)),
            "interview_to_offer": rate(len(offered), len(interviewed)),
            "response_rate": rate(len(responded), len(submitted)),
        },
        "definitions": {
            "application_to_interview": "Submitted (acknowledged) applications in range that reached Interview or "
            "Offer ÷ submitted applications in range.",
            "interview_to_offer": "Applications that reached Offer ÷ applications that reached Interview.",
            "response_rate": "Submitted applications with any employer response (status change past Submitted or "
            "a classified reply) ÷ submitted applications.",
            "null": "Shown as — when the denominator is zero.",
        },
        "status_breakdown": dict(Counter(a.status for a in in_range)),
        "top_companies": [
            {"company": c, "applications": n} for c, n in Counter(a.job.company.name for a in in_range).most_common(8)
        ],
        "top_skills": [{"skill": s, "jobs": n} for s, n in skills.most_common(12)],
        "resume_progress": [
            {"name": r.name, "date": r.created_at, "score": (r.quality or {}).get("score")}
            for r in resumes
            if r.parse_status == "parsed"
        ],
        "stage_durations_days": {s: round(median(v), 1) for s, v in durations.items() if v},
    }
