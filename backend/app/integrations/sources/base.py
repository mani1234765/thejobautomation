"""Job source adapters: official, public job-board APIs only.

Each adapter fetches one company board and normalises postings into `NormalizedJob`. Adapters never
scrape HTML pages, never authenticate as a user and never bypass rate limits or CAPTCHAs.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.domain import job_parsing as jp

USER_AGENT = "JobPilot/0.1 (job-search assistant; public job-board API client)"
log = logging.getLogger(__name__)

# Retry policy for transient failures (timeouts, connection errors, HTTP 5xx and 429). Attempts are capped
# and so is the total time, because a sync can run inside a user's request.
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 8.0
RETRY_AFTER_CAP_SECONDS = 30.0
TOTAL_BUDGET_SECONDS = 45.0
_sleep: Callable[[float], None] = time.sleep  # replaced in tests
_monotonic: Callable[[], float] = time.monotonic


class SourceError(RuntimeError):
    pass


def safe_url(value: object) -> str | None:
    """The URL if it is an absolute http(s) URL with a host, else None (rejects javascript:, data:, etc.)."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > 2000 or re.search(r"[\x00-\x20\x7f]", url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return url


@dataclass
class Skipped:
    external_id: str | None
    reason: str


@dataclass
class FetchResult:
    """What an adapter got from a board: valid listings, listings it skipped (and why), and how many the
    board returned. Skipped listings with a known id are never closed by the sync."""

    company: str
    jobs: list[NormalizedJob]
    skipped: list[Skipped] = field(default_factory=list)
    raw_count: int = 0

    @property
    def skipped_ids(self) -> set[str]:
        return {s.external_id for s in self.skipped if s.external_id}

    @property
    def unidentified_skips(self) -> int:
        return sum(1 for s in self.skipped if not s.external_id)


def build_result(
    provider: str, company: str, items: Iterable[Any], make: Callable[[dict[str, Any]], NormalizedJob]
) -> FetchResult:
    """Validate each raw listing and build it, skipping (not crashing on) malformed ones and repeated ids."""
    res = FetchResult(company=company, jobs=[])
    seen: set[str] = set()
    for raw in items:
        res.raw_count += 1
        if not isinstance(raw, dict):
            res.skipped.append(Skipped(None, "listing is not an object"))
            continue
        rid = raw.get("id")
        ext = str(rid).strip() if isinstance(rid, (str, int)) and not isinstance(rid, bool) else ""
        if not ext:
            res.skipped.append(Skipped(None, "listing has no id"))
            continue
        if ext in seen:
            res.skipped.append(Skipped(ext, "duplicate id in the same response"))
            continue
        seen.add(ext)
        try:
            job = make(raw)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.warning("job_listing_invalid", extra={"provider": provider, "id": ext, "error": repr(exc)[:200]})
            res.skipped.append(Skipped(ext, "listing could not be read"))
            continue
        url = safe_url(job.url)
        if not job.title.strip():
            res.skipped.append(Skipped(ext, "listing has no title"))
            continue
        if url is None:
            res.skipped.append(Skipped(ext, "listing has no valid http(s) URL"))
            continue
        job.url, job.apply_url = url, safe_url(job.apply_url)
        res.jobs.append(job)
    return res


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

    def fetch(self, identifier: str) -> FetchResult:
        """Return validated listings. Raises SourceError with a user-readable message when the board as a
        whole can't be read (network, HTTP error, malformed response)."""
        ...


def _retry_after(r: httpx.Response) -> float | None:
    value = r.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            return None


def _backoff(attempt: int) -> float:
    """Full-jitter exponential backoff: random in [0, min(cap, base × 2^(attempt-1))]."""
    return random.uniform(0, min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)))  # noqa: S311


def http_get_json(url: str, params: dict | None = None) -> object:
    """GET a JSON document, retrying transient failures (timeouts, connection errors, 5xx, 429) with capped
    exponential backoff and jitter, honouring Retry-After. Permanent 4xx errors are not retried."""
    started = _monotonic()
    last: str = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        delay: float | None = None
        try:
            r = httpx.get(
                url,
                params=params,
                timeout=get_settings().job_source_timeout_seconds,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                follow_redirects=True,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = f"Network error contacting the job board: {exc.__class__.__name__}"
        else:
            if r.status_code == 429:
                last = "The job board is rate limiting requests."
                delay = _retry_after(r)
            elif r.status_code >= 500:
                last = f"The job board returned HTTP {r.status_code}."
                delay = _retry_after(r)
            elif r.status_code == 404:
                raise SourceError("Board not found. Check the company's board identifier.")
            elif r.status_code >= 400:
                raise SourceError(f"The job board returned HTTP {r.status_code}.")
            else:
                try:
                    return r.json()
                except ValueError as exc:
                    raise SourceError("The job board returned an unexpected response.") from exc
        if attempt == MAX_ATTEMPTS:
            break
        wait = min(delay, RETRY_AFTER_CAP_SECONDS) if delay is not None else _backoff(attempt)
        if _monotonic() - started + wait > TOTAL_BUDGET_SECONDS:
            break
        log.info("job_board_retry", extra={"attempt": attempt, "wait_seconds": round(wait, 2), "reason": last})
        _sleep(wait)
    raise SourceError(f"{last} Gave up after {attempt} attempt{'s' if attempt > 1 else ''}.")


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
