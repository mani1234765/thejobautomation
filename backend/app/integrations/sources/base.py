"""Job source adapters: official, public job-board APIs only.

Each adapter fetches one company board and normalises postings into `NormalizedJob`. Adapters never
scrape HTML pages, never authenticate as a user and never bypass rate limits or CAPTCHAs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.config import get_settings
from app.domain import job_parsing as jp

USER_AGENT = "JobPilot/0.1 (job-search assistant; public job-board API client)"


class SourceError(RuntimeError):
    pass


@dataclass
class NormalizedJob:
    provider: str
    external_id: str
    company: str
    title: str
    url: str
    apply_url: str | None
    description_html: str
    description_text: str
    location: str | None = None
    department: str | None = None
    work_mode: str | None = None
    employment_type: str | None = None
    is_internship: bool = False
    posted_at: datetime | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_disclosure: str = "undisclosed"
    salary_text: str | None = None
    experience_min: float | None = None
    experience_max: float | None = None
    seniority: str | None = None
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)

    def enrich(self) -> NormalizedJob:
        """Derive structured facts from the description where the provider didn't supply them."""
        text = self.description_text
        if self.experience_min is None:
            self.experience_min, self.experience_max = jp.experience_range(text)
        self.seniority = jp.seniority(self.title, self.experience_min)
        if self.work_mode is None:
            self.work_mode = jp.work_mode(self.location, text[:1500])
        if self.employment_type is None:
            self.employment_type, self.is_internship = jp.employment_type(self.title, None)
        if self.salary_min is None and self.salary_max is None:
            s = jp.parse_salary_text(text)
            if s.minimum is not None:
                # Pay found only in free text is "advertised": it has not come from a structured field.
                self.salary_min, self.salary_max, self.salary_currency = s.minimum, s.maximum, s.currency
                self.salary_period, self.salary_text, self.salary_disclosure = s.period, s.text, "advertised"
        split = jp.split_skills(text)
        self.required_skills, self.preferred_skills = split.required, split.preferred
        return self


class JobSourceAdapter(Protocol):
    provider: str

    def fetch(self, identifier: str) -> tuple[str, list[NormalizedJob]]:
        """Return (company display name, jobs). Raises SourceError with a user-readable message."""
        ...


def http_get_json(url: str, params: dict | None = None) -> object:
    try:
        r = httpx.get(
            url,
            params=params,
            timeout=get_settings().job_source_timeout_seconds,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise SourceError(f"Network error contacting the job board: {exc.__class__.__name__}") from exc
    if r.status_code == 404:
        raise SourceError("Board not found. Check the company's board identifier.")
    if r.status_code == 429:
        raise SourceError("The job board is rate limiting requests. JobPilot will retry later.")
    if r.status_code >= 400:
        raise SourceError(f"The job board returned HTTP {r.status_code}.")
    try:
        return r.json()
    except ValueError as exc:
        raise SourceError("The job board returned an unexpected response.") from exc


def parse_dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=UTC)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, OverflowError):
        return None
