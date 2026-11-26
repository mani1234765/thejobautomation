"""Resume Review & Editor endpoints.

Every change is committed before the response is sent, and each response is the full, freshly read
review, so the UI only shows a correction as saved once it's in the database.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, web_only
from app.domain.resume_parser import REVIEW_THRESHOLD
from app.models import Resume, Skill, User
from app.schemas.profile import (
    CertificationIn,
    CertificationOut,
    EducationIn,
    EducationOut,
    ExperienceIn,
    ExperienceOut,
    ProjectIn,
    ProjectOut,
    SkillOut,
)
from app.security.uploads import get_file_store
from app.services import profile as profile_svc
from app.services import resume_review as svc

router = APIRouter(tags=["resume-review"])

Kind = Literal["experience", "projects", "education", "certifications"]
Target = Literal["experience", "projects", "education", "certifications", "summary", "skills"]
SCHEMA_IN: dict[str, type[BaseModel]] = {
    "experience": ExperienceIn,
    "projects": ProjectIn,
    "education": EducationIn,
    "certifications": CertificationIn,
}
SCHEMA_OUT: dict[str, type[BaseModel]] = {
    "experience": ExperienceOut,
    "projects": ProjectOut,
    "education": EducationOut,
    "certifications": CertificationOut,
}
CATEGORIES = {
    "experience": {"professional", "internship", "freelance"},
    "projects": {"academic", "personal", "professional"},
}


def _resume(db: DB, resume_id: str, user_id: str) -> Resume:
    r = get_owned(db, Resume, resume_id, user_id)
    if r.parse_status != "parsed":
        raise HTTPException(400, "This resume hasn't been parsed successfully.")
    return r


def _entry(db: DB, r: Resume, kind: str, entry_id: str) -> Any:
    row = get_owned(db, svc.MODELS[kind], entry_id, r.user_id)
    if row.resume_id != r.id:
        raise HTTPException(404, "Entry not found.")
    return row


def _validated(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return SCHEMA_IN[kind].model_validate(data).model_dump()
    except ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("body", "data", *e["loc"])} for e in exc.errors(include_url=False)]
        ) from exc


def payload(db: DB, user: User, r: Resume) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {k: [] for k in svc.KINDS}
    for kind, row in svc.entries(db, r):
        out = SCHEMA_OUT[kind].model_validate(row).model_dump(mode="json")
        out["label"] = svc.label(kind, row)
        out["issues"] = svc.issues(kind, row)
        groups[kind].append(out)
    state = r.review or {}
    x = r.extracted or {}
    assigned = set(state.get("assigned_unclassified", []))
    unclassified = [u for u in x.get("unclassified", []) if not set(u.get("source_ids", [])) & assigned]
    conf = x.get("summary_confidence")
    summary_needs_review = (
        bool(user.profile.summary) and conf is not None and conf < REVIEW_THRESHOLD and not state.get("summary_edited")
    )
    skills = db.scalars(select(Skill).where(Skill.user_id == user.id).order_by(Skill.name)).all()
    flagged = sum(e["needs_review"] for g in groups.values() for e in g)
    return {
        "resume_id": r.id,
        "imported": "imported_at" in state or any(groups.values()),
        "reviewed_at": state.get("reviewed_at"),
        "last_reextracted_at": state.get("last_reextracted_at"),
        "review_threshold": REVIEW_THRESHOLD,
        "summary": {
            "text": user.profile.summary,
            "confidence": conf,
            "review_reasons": x.get("summary_reasons", []) if summary_needs_review else [],
            "needs_review": summary_needs_review,
        },
        "entries": groups,
        "skills": [SkillOut.model_validate(s).model_dump(mode="json") for s in skills],
        "unclassified": unclassified,
        "needs_review": flagged + int(summary_needs_review) + len(unclassified),
    }


def _done(db: DB, p: Principal, r: Resume) -> dict[str, Any]:
    db.commit()
    db.refresh(r)
    return payload(db, p.user, r)


@router.get("/resumes/{resume_id}/review")
def get_review(resume_id: str, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    return payload(db, p.user, _resume(db, resume_id, p.id))


@router.post("/resumes/{resume_id}/review/import")
def start_review(resume_id: str, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    svc.import_resume(db, get_file_store(), p.user, r, replace=False)
    return _done(db, p, r)


class EntryCreate(BaseModel):
    kind: Kind
    data: dict[str, Any]


@router.post("/resumes/{resume_id}/review/entries", status_code=201)
def add_entry(resume_id: str, body: EntryCreate, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    svc.create_entry(db, p.user, r, body.kind, _validated(body.kind, body.data))
    return _done(db, p, r)


@router.put("/resumes/{resume_id}/review/entries/{kind}/{entry_id}")
def edit_entry(
    resume_id: str, kind: Kind, entry_id: str, body: dict[str, Any], db: DB, p: Principal = Depends(web_only)
) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    svc.update_entry(_entry(db, r, kind, entry_id), _validated(kind, body))
    return _done(db, p, r)


@router.delete("/resumes/{resume_id}/review/entries/{kind}/{entry_id}")
def remove_entry(resume_id: str, kind: Kind, entry_id: str, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    svc.delete_entry(db, r, _entry(db, r, kind, entry_id))
    return _done(db, p, r)


class MoveIn(BaseModel):
    to: Target
    category: str | None = None


@router.post("/resumes/{resume_id}/review/entries/{kind}/{entry_id}/move")
def move_entry(
    resume_id: str, kind: Kind, entry_id: str, body: MoveIn, db: DB, p: Principal = Depends(web_only)
) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    if body.category is not None and body.category not in CATEGORIES.get(body.to, set()):
        raise HTTPException(400, "That category doesn't apply to this section.")
    try:
        result = svc.move_entry(
            db, get_file_store(), p.user, r, _entry(db, r, kind, entry_id), body.to, category=body.category
        )
    except svc.ReviewError as exc:
        raise HTTPException(400, str(exc)) from exc
    out = _done(db, p, r)
    out["moved"] = {
        "to": body.to,
        "entry_ids": [row.id for _k, row in result["entries"]],
        "skills": result["skills"],
    }
    return out


class SplitIn(BaseModel):
    at: int = Field(ge=0)


@router.post("/resumes/{resume_id}/review/entries/{kind}/{entry_id}/split")
def split_entry(
    resume_id: str, kind: Kind, entry_id: str, body: SplitIn, db: DB, p: Principal = Depends(web_only)
) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    try:
        svc.split_entry(db, p.user, r, _entry(db, r, kind, entry_id), body.at)
    except svc.ReviewError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _done(db, p, r)


class MergeIn(BaseModel):
    kind: Kind
    ids: list[str] = Field(min_length=2, max_length=10)


@router.post("/resumes/{resume_id}/review/merge")
def merge_entries(resume_id: str, body: MergeIn, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    rows = [_entry(db, r, body.kind, i) for i in dict.fromkeys(body.ids)]
    try:
        svc.merge_entries(db, r, rows)
    except svc.ReviewError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _done(db, p, r)


class AssignIn(BaseModel):
    to: Target
    source_ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/resumes/{resume_id}/review/assign")
def assign_unclassified(resume_id: str, body: AssignIn, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    if body.to == "skills":
        raise HTTPException(400, "Add skills individually in the Skills section.")
    try:
        svc.create_from_source(db, get_file_store(), p.user, r, body.to, body.source_ids)
    except svc.ReviewError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _done(db, p, r)


class SummaryIn(BaseModel):
    text: str | None = Field(default=None, max_length=4000)


@router.put("/resumes/{resume_id}/review/summary")
def edit_summary(resume_id: str, body: SummaryIn, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    svc.set_summary(p.user, r, body.text)
    return _done(db, p, r)


class SkillAdd(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.post("/resumes/{resume_id}/review/skills", status_code=201)
def add_skill(resume_id: str, body: SkillAdd, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    profile_svc.upsert_skill(db, p.id, body.name.strip(), source="user", verified=True)
    return _done(db, p, r)


@router.delete("/resumes/{resume_id}/review/skills/{skill_id}")
def remove_skill(resume_id: str, skill_id: str, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    skill = get_owned(db, Skill, skill_id, p.id)
    svc.record_skill_deletion(db, p.id, skill.name)
    db.delete(skill)
    return _done(db, p, r)


@router.post("/resumes/{resume_id}/review/confirm")
def confirm_review(resume_id: str, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    svc.confirm(db, p.user, r)
    return _done(db, p, r)


@router.get("/resumes/{resume_id}/reextract")
def preview_reextract(resume_id: str, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    """What a re-extraction would change. Nothing is saved."""
    r = _resume(db, resume_id, p.id)
    return svc.reextract_preview(db, get_file_store(), p.user, r)


class ReextractIn(BaseModel):
    remove_stale: bool = False
    accept_summary: bool = False


@router.post("/resumes/{resume_id}/reextract")
def apply_reextract(resume_id: str, body: ReextractIn, db: DB, p: Principal = Depends(web_only)) -> dict[str, Any]:
    r = _resume(db, resume_id, p.id)
    try:
        changes = svc.reextract_apply(
            db, get_file_store(), p.user, r, remove_stale=body.remove_stale, accept_summary=body.accept_summary
        )
    except svc.ReviewError as exc:
        raise HTTPException(400, str(exc)) from exc
    out = _done(db, p, r)
    out["changes"] = changes
    return out
