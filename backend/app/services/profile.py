from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select
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


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def apply_resume(db: Session, user: User, resume: Resume, parsed: ParsedResume, *, replace: bool) -> dict:
    """Prefill the profile from a parsed resume. Everything imported is marked *unverified* until the
    user reviews it. With `replace`, rows previously imported from this resume are replaced."""
    uid = user.id
    if replace:
        for model in (Experience, Project, Education, Certification):
            db.execute(delete(model).where(model.user_id == uid, model.resume_id == resume.id))

    p = user.profile
    c = parsed.contact
    filled: list[str] = []
    for field, value in (
        ("full_name", c.get("name")),
        ("phone", c.get("phone")),
        ("location", c.get("location")),
        ("linkedin_url", c.get("linkedin")),
        ("summary", parsed.summary),
    ):
        if value and not getattr(p, field):
            if field == "linkedin_url" and not value.startswith("http"):
                value = "https://" + value
            setattr(p, field, value)
            filled.append(field)
    if p.years_experience is None:
        p.years_experience = parsed.years_experience
        filled.append("years_experience")

    for e in parsed.experiences:
        db.add(
            Experience(
                user_id=uid,
                resume_id=resume.id,
                category=e.category,
                employer=e.employer,
                title=e.title,
                location=e.location,
                start_date=_d(e.start_date),
                end_date=_d(e.end_date),
                is_current=e.is_current,
                bullets=e.bullets,
                verified=False,
            )
        )
    for pr in parsed.projects:
        db.add(
            Project(
                user_id=uid,
                resume_id=resume.id,
                category=pr.category,
                name=pr.name,
                description=pr.description,
                technologies=pr.technologies,
                url=pr.url,
                verified=False,
            )
        )
    for ed in parsed.education:
        db.add(
            Education(
                user_id=uid,
                resume_id=resume.id,
                institution=ed.institution,
                degree=ed.degree,
                field=ed.field,
                start_date=_d(ed.start_date),
                end_date=_d(ed.end_date),
                grade=ed.grade,
                verified=False,
            )
        )
    for cert in parsed.certifications:
        db.add(
            Certification(
                user_id=uid, resume_id=resume.id, name=cert["name"], issuer=cert.get("issuer"), verified=False
            )
        )
    for name, evidence in parsed.skills.items():
        upsert_skill(db, uid, name, source="resume", verified=False, evidence=evidence)

    audit.record(
        db,
        user_id=uid,
        actor="user",
        action="profile.prefilled_from_resume",
        entity_type="resume",
        entity_id=resume.id,
        meta={"experiences": len(parsed.experiences), "skills": len(parsed.skills)},
    )
    return {
        "filled_fields": filled,
        "experiences": len(parsed.experiences),
        "projects": len(parsed.projects),
        "education": len(parsed.education),
        "skills": len(parsed.skills),
        "certifications": len(parsed.certifications),
    }


def verify_all(db: Session, user: User) -> None:
    uid = user.id
    for model in (Experience, Project, Education, Certification, Skill):
        for row in db.scalars(select(model).where(model.user_id == uid, model.verified.is_(False))):
            row.verified = True
    user.profile.verified_at = utcnow()
    audit.record(db, user_id=uid, actor="user", action="profile.verified")
