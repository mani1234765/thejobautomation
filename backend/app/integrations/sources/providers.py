"""Greenhouse Job Board API, Lever Postings API and Ashby Posting API adapters."""

from __future__ import annotations

from typing import Any

from app.domain import job_parsing as jp
from app.integrations.sources.base import NormalizedJob, SourceError, http_get_json, parse_dt

_ASHBY_TYPES = {
    "FullTime": "full_time",
    "PartTime": "part_time",
    "Contract": "contract",
    "Intern": "internship",
    "Temporary": "contract",
}


class GreenhouseAdapter:
    provider = "greenhouse"

    def fetch(self, identifier: str) -> tuple[str, list[NormalizedJob]]:
        data = http_get_json(f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs", {"content": "true"})
        if not isinstance(data, dict) or "jobs" not in data:
            raise SourceError("Unexpected Greenhouse response.")
        jobs: list[NormalizedJob] = []
        company = identifier
        for j in data["jobs"]:
            company = j.get("company_name") or company
            html, text = jp.clean_html(j.get("content"))
            depts = j.get("departments") or []
            location = (j.get("location") or {}).get("name")
            jobs.append(
                NormalizedJob(
                    provider=self.provider,
                    external_id=str(j["id"]),
                    company=company,
                    title=j.get("title", "").strip(),
                    url=j.get("absolute_url") or "",
                    apply_url=j.get("absolute_url"),
                    description_html=html,
                    description_text=text,
                    location=location,
                    department=depts[0]["name"] if depts else None,
                    posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
                ).enrich()
            )
        return company, jobs


class LeverAdapter:
    provider = "lever"

    def fetch(self, identifier: str) -> tuple[str, list[NormalizedJob]]:
        data = http_get_json(f"https://api.lever.co/v0/postings/{identifier}", {"mode": "json"})
        if not isinstance(data, list):
            raise SourceError("Unexpected Lever response.")
        company = identifier.replace("-", " ").title()
        jobs: list[NormalizedJob] = []
        for j in data:
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
                external_id=j["id"],
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
                # Structured pay published by the employer through their ATS → treated as verified.
                job.salary_min, job.salary_max = salary.get("min"), salary.get("max")
                job.salary_currency = salary.get("currency")
                job.salary_period = {
                    "per-year-salary": "year",
                    "per-month-salary": "month",
                    "per-hour-wage": "hour",
                }.get(salary.get("interval", ""), "year")
                job.salary_disclosure = "verified"
            jobs.append(job.enrich())
        return company, jobs


class AshbyAdapter:
    provider = "ashby"

    def fetch(self, identifier: str) -> tuple[str, list[NormalizedJob]]:
        data = http_get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{identifier}", {"includeCompensation": "true"}
        )
        if not isinstance(data, dict) or "jobs" not in data:
            raise SourceError("Unexpected Ashby response.")
        company = identifier.replace("-", " ").title()
        jobs: list[NormalizedJob] = []
        for j in data["jobs"]:
            if j.get("isListed") is False:
                continue
            html, text = jp.clean_html(j.get("descriptionHtml"))
            et = _ASHBY_TYPES.get(j.get("employmentType") or "")
            wm = (j.get("workplaceType") or "").lower()
            job = NormalizedJob(
                provider=self.provider,
                external_id=j["id"],
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
                s = jp.parse_salary_text(summary)
                if s.minimum is not None:
                    job.salary_min, job.salary_max, job.salary_currency = s.minimum, s.maximum, s.currency
                    job.salary_period, job.salary_text, job.salary_disclosure = s.period, summary, "verified"
            jobs.append(job.enrich())
        return company, jobs


ADAPTERS = {a.provider: a for a in (GreenhouseAdapter(), LeverAdapter(), AshbyAdapter())}


def get_adapter(provider: str):
    try:
        return ADAPTERS[provider]
    except KeyError as exc:
        raise SourceError(f"Unsupported job source '{provider}'.") from exc
