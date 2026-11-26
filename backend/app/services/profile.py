from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import taxonomy
from app.domain.matching import CandidateProfile
from app.domain.resume_parser import ParsedResume
from app.domain.salary_policy import SalaryPolicy
from app.models import Certification, Education, Experience, Project, Resume, Skill, User
from app.schemas.profile import FullProfile, ProfileOut
from app.services import audit


def full_profile(db: Session, user: User) -> FullProfile:
    uid = user.id
    exps = db.scalars(
        select(Experience)
        .where(Experience.user_id == uid)
        .order_by(Experience.is_current.desc(), Experience.start_date.desc())
    ).all()
    projects = db.scalars(select(Project).where(Project.user_id == uid).order_by(Project.created_at)).all()
    edu = db.scalars(select(Education).where(Education.user_id == uid).order_by(Education.end_date.desc())).all()
    certs = db.scalars(select(Certification).where(Certification.user_id == uid)).all()
    skills = db.scalars(select(Skill).where(Skill.user_id == uid).order_by(Skill.name)).all()
    return FullProfile(
        profile=ProfileOut.model_validate(user.profile),
        experiences=exps,
        projects=projects,
        education=edu,
        certifications=certs,
        skills=skills,  # type: ignore[arg-type]
        completeness=completeness(user, len(exps) + len(projects), len(edu), len(skills)),
    )


def completeness(user: User, n_work: int, n_edu: int, n_skills: int) -> dict:
    p = user.profile
    checks = {
        "name": bool(p.full_name),
        "phone": bool(p.phone),
        "location": bool(p.location),
        "work_authorization": bool(p.work_authorization),
        "target_titles": bool(p.target_titles),
        "preferences": bool(p.work_modes and p.employment_types and p.experience_levels),
        "experience_or_projects": n_work > 0,
        "education": n_edu > 0,
        "skills": n_skills >= 5,
        "years_experience": p.years_experience is not None,
        "verified": p.verified_at is not None,
    }
    return {"percent": round(100 * sum(checks.values()) / len(checks)), "checks": checks}


def salary_policy(user: User) -> SalaryPolicy:
    s = user.settings
    return SalaryPolicy(
        min_ctc_lpa=s.min_ctc_lpa,
        allow_undisclosed_salary=s.allow_undisclosed_salary,
        min_stipend_monthly_inr=s.min_stipend_monthly_inr,
        allow_undisclosed_stipend=s.allow_undisclosed_stipend,
    )


def candidate_profile(db: Session, user: User) -> CandidateProfile:
    """Matching input built only from stored profile data. Verified skills are marked as such."""
    uid, p = user.id, user.profile
    skills: dict[str, str] = {}
    for s in db.scalars(select(Skill).where(Skill.user_id == uid)):
        skills[s.name] = "verified" if s.verified else s.source
    for pr in db.scalars(select(Project).where(Project.user_id == uid)):
        for t in pr.technologies:
            skills.setdefault(taxonomy.normalize_skill(t), "project")
    past_titles = [t for (t,) in db.execute(select(Experience.title).where(Experience.user_id == uid)) if t]
    return CandidateProfile(
        skills=skills,
        years_experience=p.years_experience,
        target_titles=list(p.target_titles),
        past_titles=past_titles,
        preferred_locations=list(p.preferred_locations),
        work_modes=list(p.work_modes),
        experience_levels=list(p.experience_levels),
        employment_types=list(p.employment_types),
    )


def upsert_skill(
    db: Session, user_id: str, name: str, *, source: str, verified: bool, evidence: list[str] | None = None
) -> Skill:
    canon = taxonomy.normalize_skill(name)
    skill = db.scalar(select(Skill).where(Skill.user_id == user_id, Skill.name == canon))
    if skill is None:
        skill = Skill(
            user_id=user_id,
            name=canon,
            category=taxonomy.category_of(canon),
            source=source,
            verified=verified,
            evidence=(evidence or [])[:3],
        )
        db.add(skill)
    else:
        skill.verified = skill.verified or verified
        if evidence and not skill.evidence:
            skill.evidence = evidence[:3]
    return skill


def apply_resume(db: Session, user: User, resume: Resume, parsed: ParsedResume, *, replace: bool) -> dict:
    """Prefill the profile from a parsed resume. Everything imported starts *unverified*.

    Delegates to the review service, which preserves the user's corrections: edited entries are kept,
    deleted ones stay deleted. With `replace`, unedited entries the extraction no longer finds are removed.
    """
    from app.security.uploads import get_file_store
    from app.services.resume_review import import_resume

    return import_resume(db, get_file_store(), user, resume, replace=replace, parsed=parsed)


def verify_all(db: Session, user: User) -> None:
    uid = user.id
    for model in (Experience, Project, Education, Certification, Skill):
        for row in db.scalars(select(model).where(model.user_id == uid)):
            row.verified = True
            if model is not Skill:
                row.needs_review, row.review_reasons = False, []
    user.profile.verified_at = utcnow()
    audit.record(db, user_id=uid, actor="user", action="profile.verified")
