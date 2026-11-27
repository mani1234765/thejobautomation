"""Greenhouse Job Board API, Lever Postings API and Ashby Posting API adapters."""

from __future__ import annotations

from typing import Any

from app.domain import fx
from app.domain import job_parsing as jp
from app.integrations.sources.base import (
    FetchResult,
    NormalizedJob,
    SourceError,
    build_result,
    http_get_json,
    parse_dt,
)

_ASHBY_TYPES = {
    "FullTime": "full_time",
    "PartTime": "part_time",
    "Contract": "contract",
    "Intern": "internship",
    "Temporary": "contract",
}


def _items(data: object, key: str | None, provider: str) -> list:
    """The list of listings in a response, or SourceError when the response isn't the expected shape."""
    if key is not None:
        if not isinstance(data, dict):
            raise SourceError(f"Unexpected {provider} response.")
        data = data.get(key)
    items = data
    if not isinstance(items, list):
        raise SourceError(f"Unexpected {provider} response.")
    return items


class GreenhouseAdapter:
    provider = "greenhouse"

    def fetch(self, identifier: str) -> FetchResult:
        data = http_get_json(f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs", {"content": "true"})
        items = _items(data, "jobs", "Greenhouse")
        company = next(
            (j["company_name"] for j in items if isinstance(j, dict) and isinstance(j.get("company_name"), str)),
            identifier,
        )

        def make(j: dict[str, Any]) -> NormalizedJob:
            html, text = jp.clean_html(j.get("content"))
            depts = j.get("departments") or []
            return NormalizedJob(
                provider=self.provider,
                external_id=str(j["id"]),
                company=company,
                title=(j.get("title") or "").strip(),
                url=j.get("absolute_url") or "",
                apply_url=j.get("absolute_url"),
                description_html=html,
                description_text=text,
                location=(j.get("location") or {}).get("name"),
                department=depts[0]["name"] if depts and isinstance(depts[0], dict) else None,
                posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
            ).enrich()

        return build_result(self.provider, company, items, make)


class LeverAdapter:
    provider = "lever"

    def fetch(self, identifier: str) -> FetchResult:
        data = http_get_json(f"https://api.lever.co/v0/postings/{identifier}", {"mode": "json"})
        items = _items(data, None, "Lever")
        company = identifier.replace("-", " ").title()

        def make(j: dict[str, Any]) -> NormalizedJob:
            cats: dict[str, Any] = j.get("categories") or {}
            parts = [j.get("description") or ""]
            for lst in j.get("lists") or []:
                parts.append(f"<h3>{lst.get('text', '')}</h3><ul>{lst.get('content', '')}</ul>")
            parts.append(j.get("additional") or "")
            html, text = jp.clean_html("".join(parts))
            et, intern = jp.employment_type(j.get("text", ""), cats.get("commitment"))
            salary = j.get("salaryRange") or {}
            job = NormalizedJob(
                provider=self.provider,
                external_id=str(j["id"]),
                company=company,
                title=(j.get("text") or "").strip(),
                url=j.get("hostedUrl") or "",
                apply_url=j.get("applyUrl"),
                description_html=html,
                description_text=text,
                location=cats.get("location"),
                department=cats.get("team"),
                work_mode={"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}.get(
                    (j.get("workplaceType") or "").lower()
                ),
                employment_type=et,
                is_internship=intern,
                posted_at=parse_dt(j.get("createdAt")),
            )
            if salary.get("min") or salary.get("max"):
                # Structured pay published by the employer through their ATS: the amounts are verified. The
                # currency is identified explicitly (never assumed); when Lever omits it, eligibility sends the
                # job to manual review with SAL_CURRENCY_UNKNOWN.
                job.salary_min, job.salary_max = salary.get("min"), salary.get("max")
                job.salary_currency = fx.identify(salary.get("currency"))
                job.salary_period = {
                    "per-year-salary": "year",
                    "per-month-salary": "month",
                    "per-hour-wage": "hour",
                }.get(salary.get("interval") or "")  # an unknown or missing interval stays unknown
                job.salary_disclosure = "verified"
            return job.enrich()

        return build_result(self.provider, company, items, make)


class AshbyAdapter:
    provider = "ashby"

    def fetch(self, identifier: str) -> FetchResult:
        data = http_get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{identifier}", {"includeCompensation": "true"}
        )
        items = _items(data, "jobs", "Ashby")
        company = identifier.replace("-", " ").title()
        # Unlisted postings are not open roles: they are left out (and closed if previously stored).
        listed = [j for j in items if not (isinstance(j, dict) and j.get("isListed") is False)]

        def make(j: dict[str, Any]) -> NormalizedJob:
            html, text = jp.clean_html(j.get("descriptionHtml"))
            et = _ASHBY_TYPES.get(j.get("employmentType") or "")
            wm = (j.get("workplaceType") or "").lower()
            job = NormalizedJob(
                provider=self.provider,
                external_id=str(j["id"]),
                company=company,
                title=(j.get("title") or "").strip(),
                url=j.get("jobUrl") or "",
                apply_url=j.get("applyUrl"),
                description_html=html,
                description_text=text,
                location=j.get("location"),
                department=j.get("department"),
                work_mode="remote"
                if j.get("isRemote") and not wm
                else {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(wm),
                employment_type=et,
                is_internship=et == "internship",
                posted_at=parse_dt(j.get("publishedAt")),
            )
            comp = j.get("compensation") or {}
            summary = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
            if summary:
                sp = jp.parse_salary_text(summary)
                if sp.minimum is not None:
                    job.salary_min, job.salary_max, job.salary_currency = sp.minimum, sp.maximum, sp.currency
                    job.salary_period, job.salary_text, job.salary_disclosure = sp.period, summary, "verified"
            return job.enrich()

        return build_result(self.provider, company, listed, make)


ADAPTERS = {a.provider: a for a in (GreenhouseAdapter(), LeverAdapter(), AshbyAdapter())}


def get_adapter(provider: str):
    try:
        return ADAPTERS[provider]
    except KeyError as exc:
        raise SourceError(f"Unsupported job source '{provider}'.") from exc
