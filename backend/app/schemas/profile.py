from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

WorkMode = Literal["remote", "hybrid", "onsite"]
ExperienceLevel = Literal["fresher", "internship", "0-1", "1-2", "2-3", "3-5", "5+"]
EmploymentType = Literal["full_time", "part_time", "contract", "internship"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=200)
    headline: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=4000)
    work_authorization: str | None = Field(default=None, max_length=300)
    career_objectives: str | None = Field(default=None, max_length=4000)
    linkedin_url: HttpUrl | None = None
    portfolio_url: HttpUrl | None = None
    target_titles: list[str] = Field(default_factory=list, max_length=15)
    preferred_locations: list[str] = Field(default_factory=list, max_length=15)
    work_modes: list[WorkMode] = Field(default_factory=list)
    experience_levels: list[ExperienceLevel] = Field(default_factory=list)
    employment_types: list[EmploymentType] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list, max_length=15)
    years_experience: float | None = Field(default=None, ge=0, le=60)
    salary_expectation_lpa: float | None = Field(default=None, ge=0, le=1000)
    notice_period: str | None = Field(default=None, max_length=100)

    @field_validator("target_titles", "preferred_locations", "industries")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            s = item.strip()[:120]
            if s and s.lower() not in {o.lower() for o in out}:
                out.append(s)
        return out


class ProfileOut(ORM):
    full_name: str | None
    phone: str | None
    location: str | None
    headline: str | None
    summary: str | None
    work_authorization: str | None
    career_objectives: str | None
    linkedin_url: str | None
    portfolio_url: str | None
    target_titles: list[str]
    preferred_locations: list[str]
    work_modes: list[str]
    experience_levels: list[str]
    employment_types: list[str]
    industries: list[str]
    years_experience: float | None
    salary_expectation_lpa: float | None
    notice_period: str | None
    verified_at: datetime | None


class SettingsIn(BaseModel):
    min_ctc_lpa: float = Field(ge=0, le=500)
    allow_undisclosed_salary: bool
    min_stipend_monthly_inr: float | None = Field(default=None, ge=0, le=1_000_000)
    allow_undisclosed_stipend: bool
    daily_application_limit: int = Field(ge=1, le=100)
    require_approval: bool
    min_match_score: int = Field(ge=0, le=100)
    ai_model_override: str | None = Field(default=None, max_length=120)
    notify_in_app: bool
    notify_email: bool
    notify_whatsapp: bool
    whatsapp_number: str | None = Field(default=None, pattern=r"^\+?[1-9]\d{7,14}$")
    daily_summary: bool

    @field_validator("require_approval")
    @classmethod
    def _approval_required(cls, v: bool) -> bool:
        # Product rule: submissions always require explicit approval. The flag exists so the policy is
        # visible in settings, but it cannot be turned off.
        if not v:
            raise ValueError("Application approval cannot be disabled.")
        return v


class SettingsOut(ORM, SettingsIn):
    pass


class ExperienceIn(BaseModel):
    category: Literal["professional", "internship", "freelance"]
    employer: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    bullets: list[str] = Field(default_factory=list, max_length=30)
    verified: bool = True


class ExperienceOut(ORM, ExperienceIn):
    id: str
    resume_id: str | None


class ProjectIn(BaseModel):
    category: Literal["academic", "personal", "professional"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    technologies: list[str] = Field(default_factory=list, max_length=40)
    url: str | None = Field(default=None, max_length=500)
    verified: bool = True


class ProjectOut(ORM, ProjectIn):
    id: str
    resume_id: str | None


class EducationIn(BaseModel):
    institution: str = Field(min_length=1, max_length=300)
    degree: str | None = Field(default=None, max_length=200)
    field: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    grade: str | None = Field(default=None, max_length=60)
    verified: bool = True


class EducationOut(ORM, EducationIn):
    id: str
    resume_id: str | None


class CertificationIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    issuer: str | None = Field(default=None, max_length=200)
    issued_on: date | None = None
    verified: bool = True


class CertificationOut(ORM, CertificationIn):
    id: str
    resume_id: str | None


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    verified: bool = True


class SkillOut(ORM):
    id: str
    name: str
    category: str | None
    source: str
    verified: bool
    evidence: list[str]


class FullProfile(BaseModel):
    profile: ProfileOut
    experiences: list[ExperienceOut]
    projects: list[ProjectOut]
    education: list[EducationOut]
    certifications: list[CertificationOut]
    skills: list[SkillOut]
    completeness: dict
