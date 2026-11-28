from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.domain.salary_policy import (
    APPRENTICE_CURRENCIES,
    MAX_APPRENTICE_STIPEND,
    MAX_DAILY_APPLICATIONS,
    MAX_MIN_CTC_LPA,
)

WorkMode = Literal["remote", "hybrid", "onsite"]
ExperienceLevel = Literal["fresher", "internship", "0-1", "1-2", "2-3", "3-5", "5+"]
EmploymentType = Literal["full_time", "part_time", "contract", "internship", "freelance", "temporary", "apprenticeship"]


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
    # Minimum annual CTC in ₹ lakh (LPA). A verified salary minimum must be strictly greater than this.
    min_ctc_lpa: float = Field(ge=0, le=MAX_MIN_CTC_LPA, allow_inf_nan=False)
    allow_undisclosed_salary: bool
    min_stipend_monthly_inr: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    allow_undisclosed_stipend: bool
    daily_application_limit: int = Field(ge=1, le=MAX_DAILY_APPLICATIONS)
    require_approval: bool
    min_match_score: int = Field(ge=0, le=100)
    ai_model_override: str | None = Field(default=None, max_length=120)
    notify_in_app: bool
    notify_email: bool
    notify_whatsapp: bool
    whatsapp_number: str | None = Field(default=None, pattern=r"^\+?[1-9]\d{7,14}$")
    daily_summary: bool
    # Apprenticeship stipend policy. Optional in requests: a client that doesn't send them keeps the stored values.
    apprentice_min_stipend: float | None = Field(default=None, ge=0, le=MAX_APPRENTICE_STIPEND, allow_inf_nan=False)
    apprentice_stipend_currency: Literal[APPRENTICE_CURRENCIES] = "INR"  # type: ignore[valid-type]
    apprentice_stipend_period: Literal["month", "year"] = "month"
    apprentice_advertised_stipend: Literal["compare", "review"] = "compare"
    apprentice_unverified_outcome: Literal["pass", "review"] = "pass"

    @field_validator("min_ctc_lpa")
    @classmethod
    def _two_decimals(cls, v: float) -> float:
        # ₹0.01 LPA = ₹1,000: finer precision isn't meaningful and would make boundaries confusing.
        return round(v, 2)

    @field_validator("require_approval")
    @classmethod
    def _approval_required(cls, v: bool) -> bool:
        # Product rule: submissions always require explicit approval. The flag exists so the policy is
        # visible in settings, but it cannot be turned off.
        if not v:
            raise ValueError("Application approval cannot be disabled.")
        return v


class SettingsOut(ORM, SettingsIn):
    # None only when a stored currency was invalid (reported in `normalized`; never replaced by a guess).
    apprentice_stipend_currency: str | None = "INR"  # type: ignore[assignment]
    # Stored values that were out of range and are shown (and applied) as the nearest valid value:
    # [{field, stored, effective, reason}]. Saving Settings stores the effective values.
    normalized: list[dict] = Field(default_factory=list)


def _clean_lines(v: list[str]) -> list[str]:
    return [x.strip() for x in v if x and x.strip()]


class _Dated(BaseModel):
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _dates_in_order(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("The end date is before the start date.")
        return self


class ReviewOut(ORM):
    """Provenance and review state shared by entries extracted from a resume."""

    source_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None
    review_reasons: list[str] = Field(default_factory=list)
    needs_review: bool = False
    user_edited: bool = False


class ExperienceIn(_Dated):
    category: Literal["professional", "internship", "freelance"]
    employer: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    is_current: bool = False
    bullets: list[str] = Field(default_factory=list, max_length=30)
    verified: bool = True

    _bullets = field_validator("bullets")(classmethod(lambda cls, v: _clean_lines(v)))

    @model_validator(mode="after")
    def _current_has_no_end(self):
        if self.is_current:
            self.end_date = None
        return self


class ExperienceOut(ReviewOut, ExperienceIn):
    id: str
    resume_id: str | None


class ProjectIn(BaseModel):
    category: Literal["academic", "personal", "professional"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    technologies: list[str] = Field(default_factory=list, max_length=40)
    url: str | None = Field(default=None, max_length=500)
    verified: bool = True


class ProjectOut(ReviewOut, ProjectIn):
    id: str
    resume_id: str | None


class EducationIn(_Dated):
    institution: str = Field(min_length=1, max_length=300)
    degree: str | None = Field(default=None, max_length=200)
    field: str | None = Field(default=None, max_length=200)
    grade: str | None = Field(default=None, max_length=60)
    details: list[str] = Field(default_factory=list, max_length=30)
    verified: bool = True

    _details = field_validator("details")(classmethod(lambda cls, v: _clean_lines(v)))


class EducationOut(ReviewOut, EducationIn):
    id: str
    resume_id: str | None


class CertificationIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    issuer: str | None = Field(default=None, max_length=200)
    issued_on: date | None = None
    details: list[str] = Field(default_factory=list, max_length=30)
    verified: bool = True

    _details = field_validator("details")(classmethod(lambda cls, v: _clean_lines(v)))


class CertificationOut(ReviewOut, CertificationIn):
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
