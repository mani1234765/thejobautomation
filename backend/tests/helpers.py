"""Test doubles. These are used only by the test suite — production code paths never import them."""

from __future__ import annotations

from typing import Any

from app.ai.provider import LLMResult
from app.integrations.sources.base import NormalizedJob

JD_BACKEND = """About the role
We're hiring a Backend Engineer to build payment APIs.
Requirements
• 2-4 years of experience building services in Python
• Strong PostgreSQL and AWS experience
• Docker in production
Nice to have
• Kubernetes
Compensation: ₹18 - 26 LPA"""

JD_SALES = """Requirements\n• 5+ years of experience in enterprise sales\n• Salesforce and CRM expertise"""


def job(ext_id: str, title: str, text: str, **kw: Any) -> NormalizedJob:
    return NormalizedJob(
        provider="greenhouse",
        external_id=ext_id,
        company="Acme Payments",
        title=title,
        url=f"https://boards.greenhouse.io/acme/jobs/{ext_id}",
        apply_url=f"https://boards.greenhouse.io/acme/jobs/{ext_id}#app",
        description_html=f"<p>{text}</p>",
        description_text=text,
        location=kw.pop("location", "Bengaluru, India"),
        **kw,
    ).enrich()


class FakeAdapter:
    """Stands in for a job board in tests (no network)."""

    provider = "greenhouse"

    def __init__(self, jobs: list[NormalizedJob] | None = None, error: str | None = None) -> None:
        self.jobs = (
            jobs
            if jobs is not None
            else [job("1", "Backend Engineer", JD_BACKEND), job("2", "Enterprise Account Executive", JD_SALES)]
        )
        self.error = error

    def fetch(self, identifier: str):
        if self.error:
            from app.integrations.sources.base import SourceError

            raise SourceError(self.error)
        return "Acme Payments", self.jobs


class ScriptedLLM:
    """Deterministic provider double: returns queued results in order and records requests."""

    name = "anthropic"
    model = "claude-opus-5-5"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def complete(self, *, system, messages, json_schema=None, tools=None, max_tokens=None) -> LLMResult:
        self.requests.append(
            {
                "system": system,
                "messages": [dict(m) for m in messages],
                "schema": json_schema,
                "tools": [t.name for t in tools or []],
            }
        )
        r = self.responses.pop(0)
        return LLMResult(
            text=r.get("text", ""),
            provider=self.name,
            model=self.model,
            input_tokens=1000,
            output_tokens=200,
            tool_calls=r.get("tool_calls", []),
            data=r.get("data"),
            raw_assistant=[{"type": "text", "text": r.get("text", "")}],
        )
