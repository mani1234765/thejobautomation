"""AI Career Assistant with a controlled tool layer.

Every tool is read-only and scoped to the signed-in user. The assistant can *draft* text but has no
tool that submits applications, sends messages or changes settings — those stay behind explicit
user actions in the UI. Tool results are passed back wrapped as untrusted data.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import service as ai
from app.ai.guards import untrusted
from app.ai.provider import ToolSpec
from app.db import utcnow
from app.domain import taxonomy
from app.models import AIConversation, AIMessage, Application, Job, JobMatch, Resume, User
from app.services import jobs as jobs_svc
from app.services.profile import full_profile

MAX_TOOL_ROUNDS = 6
HISTORY_LIMIT = 40

SYSTEM = """You are JobPilot's career assistant. You help one candidate find suitable jobs, understand job
descriptions, improve their resume, prepare applications and interviews, and write recruiter, referral
and follow-up messages.

Use the tools to look up the candidate's real data before answering questions about it. When you state
something about the candidate, label it: (verified) for data the user confirmed, (from resume, unverified)
for imported data, (inferred) for your reasoning, and say plainly when something is unknown.
You can draft messages and answers, but the user sends or submits them from JobPilot themselves.
Keep answers concise and practical; use short lists where helpful."""

TOOLS = [
    ToolSpec(
        "get_profile",
        "Get the candidate's profile: preferences, experience, projects, education, skills "
        "with verification status, and profile completeness.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "search_jobs",
        "Search jobs from the candidate's followed job boards, ranked by match score.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "Title, company or team keywords"},
                "location": {"type": "string"},
                "work_modes": {"type": "array", "items": {"type": "string", "enum": ["remote", "hybrid", "onsite"]}},
                "min_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "limit": {"type": "integer", "minimum": 1, "maximum": 15},
            },
        },
    ),
    ToolSpec(
        "get_job_match",
        "Get a job's description and its explained match against the candidate.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["job_id"],
            "properties": {"job_id": {"type": "string"}},
        },
    ),
    ToolSpec(
        "list_applications",
        "List the candidate's applications and their pipeline status.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "get_resume_quality",
        "Get the quality assessment of the candidate's primary resume.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "get_skill_demand",
        "Most requested skills across the candidate's discovered jobs, and which ones the candidate lacks.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
]


class ToolContext:
    def __init__(self, db: Session, user: User) -> None:
        self.db, self.user = db, user

    def run(self, name: str, args: dict[str, Any]) -> Any:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"error": f"Unknown tool {name}"}
        try:
            return fn(**args)
        except TypeError:
            return {"error": "Invalid arguments"}

    def t_get_profile(self) -> Any:
        fp = full_profile(self.db, self.user).model_dump(mode="json")
        for key in ("experiences", "projects", "education", "certifications"):
            for item in fp[key]:
                item.pop("resume_id", None)
                item.pop("id", None)
        fp["skills"] = [{"name": s["name"], "verified": s["verified"], "source": s["source"]} for s in fp["skills"]]
        return fp

    def t_search_jobs(
        self,
        query: str | None = None,
        location: str | None = None,
        work_modes: list[str] | None = None,
        min_score: int | None = None,
        limit: int = 8,
    ) -> Any:
        rows, total = jobs_svc.search(
            self.db,
            self.user,
            jobs_svc.JobQuery(
                q=query, location=location, work_modes=work_modes, min_score=min_score, page_size=min(limit, 15)
            ),
        )
        return {
            "total": total,
            "jobs": [
                {
                    "job_id": j.id,
                    "title": j.title,
                    "company": j.company.name,
                    "location": j.location,
                    "work_mode": j.work_mode,
                    "match_score": m.score if m else None,
                    "eligibility": m.eligibility.get("status") if m else None,
                    "url": j.url,
                }
                for j, m, _ in rows
            ],
        }

    def t_get_job_match(self, job_id: str) -> Any:
        try:
            job = jobs_svc.assert_visible(self.db, self.user, job_id)
        except Exception:  # noqa: BLE001
            return {"error": "Job not found"}
        jobs_svc.ensure_matches(self.db, self.user, [job.id])
        m = self.db.scalar(select(JobMatch).where(JobMatch.user_id == self.user.id, JobMatch.job_id == job.id))
        return {
            "title": job.title,
            "company": job.company.name,
            "location": job.location,
            "description": untrusted("job_description", job.description_text, 6000),
            "match": m.breakdown if m else None,
            "eligibility": m.eligibility if m else None,
        }

    def t_list_applications(self) -> Any:
        apps = self.db.scalars(
            select(Application)
            .where(Application.user_id == self.user.id)
            .order_by(Application.status_changed_at.desc())
            .limit(50)
        )
        return [
            {
                "application_id": a.id,
                "job": a.job.title,
                "company": a.job.company.name,
                "status": a.status,
                "submission_state": a.submission_state,
                "updated": a.status_changed_at.isoformat(),
            }
            for a in apps
        ]

    def t_get_resume_quality(self) -> Any:
        r = self.db.scalar(select(Resume).where(Resume.user_id == self.user.id, Resume.is_primary.is_(True)))
        if not r:
            return {"error": "No resume uploaded"}
        return {"name": r.name, "quality": r.quality, "sections": [s.canonical_type for s in r.sections]}

    def t_get_skill_demand(self) -> Any:
        return skill_demand(self.db, self.user)


def skill_demand(db: Session, user: User, limit: int = 15) -> dict[str, Any]:
    """Skill frequency across discovered jobs. Gaps are computed only from jobs that already match the
    user (score ≥ their threshold) and exclude soft skills, so recommendations stay relevant."""
    rows = db.execute(
        select(Job, JobMatch.score)
        .outerjoin(JobMatch, (JobMatch.job_id == Job.id) & (JobMatch.user_id == user.id))
        .where(jobs_svc.visible_jobs_clause(db, user.id))
    ).all()
    counter: Counter[str] = Counter()
    relevant: Counter[str] = Counter()
    threshold = user.settings.min_match_score if user.settings else 55
    n_relevant = 0
    for j, score in rows:
        skills = set(j.required_skills or []) | set(j.preferred_skills or [])
        counter.update(skills)
        if score is not None and score >= threshold:
            n_relevant += 1
            relevant.update(skills)
    have = set(jobs_svc.candidate_profile(db, user).skills)
    top = counter.most_common(limit)
    gaps = [s for s, _ in relevant.most_common(limit * 2) if s not in have and taxonomy.category_of(s) != "soft"]
    return {
        "jobs_analysed": len(rows),
        "matching_jobs_analysed": n_relevant,
        "top_skills": [
            {"skill": s, "jobs": n, "share": round(n / len(rows), 3) if rows else 0, "have": s in have} for s, n in top
        ],
        "gaps": gaps[:8],
        "gap_counts": {s: relevant[s] for s in gaps[:8]},
    }


def _history(db: Session, convo: AIConversation) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AIMessage)
            .where(AIMessage.conversation_id == convo.id)
            .order_by(AIMessage.created_at.desc())
            .limit(HISTORY_LIMIT)
        )
    )[::-1]
    while rows and rows[0].role != "user":  # always start at a user turn
        rows.pop(0)
    out: list[dict[str, Any]] = []
    for m in rows:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            out.append(
                {
                    "role": "assistant",
                    "content": m.content,
                    "tool_calls": m.tool_calls,
                    "raw": m.raw,
                    "provider": m.provider,
                    "model": m.model,
                }
            )
        else:
            meta = (m.tool_calls or [{}])[0]
            out.append({"role": "tool", "tool_call_id": meta.get("id"), "content": m.content})
    return out


def _add(db: Session, convo: AIConversation, role: str, content: str, **kw: Any) -> AIMessage:
    msg = AIMessage(conversation_id=convo.id, role=role, content=content, created_at=utcnow(), **kw)
    db.add(msg)
    db.flush()
    return msg


def send(db: Session, user: User, convo: AIConversation, text: str) -> list[AIMessage]:
    """Append a user turn, run the tool loop, persist every step append-only, return new messages."""
    created = [_add(db, convo, "user", text)]
    db.commit()
    ctx = ToolContext(db, user)
    messages = _history(db, convo)
    for _ in range(MAX_TOOL_ROUNDS):
        result = ai.complete(db, user, feature="assistant", system=SYSTEM, messages=messages, tools=TOOLS)
        assistant_msg = result.as_assistant_message()
        created.append(
            _add(
                db,
                convo,
                "assistant",
                result.text,
                tool_calls=result.tool_calls,
                raw=result.raw_assistant,
                provider=result.provider,
                model=result.model,
            )
        )
        messages.append(assistant_msg)
        if not result.tool_calls:
            break
        for call in result.tool_calls:
            output = ctx.run(call["name"], call.get("arguments") or {})
            payload = json.dumps(output, default=str)[:30_000]
            created.append(_add(db, convo, "tool", payload, tool_calls=[{"id": call["id"], "name": call["name"]}]))
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": payload})
        db.commit()
    else:
        created.append(
            _add(
                db,
                convo,
                "assistant",
                "I couldn't finish that within the tool-use limit. Try asking a narrower question.",
            )
        )
    if convo.title == "New conversation":
        convo.title = text.strip()[:80]
    convo.updated_at = utcnow()
    db.commit()
    return created
