from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, require_scope, web_only
from app.config import get_settings
from app.integrations.sources.base import SourceError
from app.models import Resume, ResumeVersion
from app.security.uploads import DOCX_MIME, UploadRejected, get_file_store, validate_resume_upload
from app.services import audit
from app.services import jobs as jobs_svc
from app.services import profile as profile_svc
from app.services import resumes as svc

router = APIRouter(tags=["resumes"])


class SectionOut(BaseModel):
    canonical_type: str
    raw_heading: str | None
    confidence: float
    text: str
    paragraph_ids: list[str]


class ResumeSummary(BaseModel):
    id: str
    name: str
    original_filename: str
    file_format: str
    size_bytes: int
    is_primary: bool
    parse_status: str
    parse_error: str | None
    parsed_at: datetime | None
    created_at: datetime
    quality_score: int | None
    versions: int


class ResumeDetail(ResumeSummary):
    sections: list[SectionOut]
    extracted: dict[str, Any]
    quality: dict[str, Any]
    formatting_preserved: bool


class VersionOut(BaseModel):
    id: str
    resume_id: str
    job_id: str | None
    label: str
    status: str
    changes: list[dict[str, Any]]
    analysis: dict[str, Any]
    generated_by: str
    created_at: datetime
    approved_at: datetime | None
    downloadable: bool


def _summary(r: Resume) -> ResumeSummary:
    return ResumeSummary(
        id=r.id,
        name=r.name,
        original_filename=r.original_filename,
        file_format=r.file_format,
        size_bytes=r.size_bytes,
        is_primary=r.is_primary,
        parse_status=r.parse_status,
        parse_error=r.parse_error,
        parsed_at=r.parsed_at,
        created_at=r.created_at,
        quality_score=(r.quality or {}).get("score"),
        versions=len(r.versions),
    )


def _detail(r: Resume) -> ResumeDetail:
    return ResumeDetail(
        **_summary(r).model_dump(),
        sections=[SectionOut.model_validate(s, from_attributes=True) for s in r.sections],
        extracted=r.extracted or {},
        quality=r.quality or {},
        formatting_preserved=r.file_format == "docx",
    )


def _version(v: ResumeVersion) -> VersionOut:
    return VersionOut(
        id=v.id,
        resume_id=v.resume_id,
        job_id=v.job_id,
        label=v.label,
        status=v.status,
        changes=v.changes,
        analysis=v.analysis,
        generated_by=v.generated_by,
        created_at=v.created_at,
        approved_at=v.approved_at,
        downloadable=v.status == "approved",
    )


@router.get("/resumes", response_model=list[ResumeSummary])
def list_resumes(db: DB, p: CurrentPrincipal) -> list[ResumeSummary]:
    rows = db.scalars(
        select(Resume).where(Resume.user_id == p.id).order_by(Resume.is_primary.desc(), Resume.created_at.desc())
    )
    return [_summary(r) for r in rows]


@router.post("/resumes", response_model=ResumeDetail, status_code=201)
async def upload_resume(
    db: DB, file: UploadFile = File(...), name: str | None = Form(default=None), p: Principal = Depends(web_only)
) -> ResumeDetail:
    # Read in chunks and stop at the limit, so an oversized upload can't exhaust memory.
    limit = get_settings().max_upload_mb * 1024 * 1024
    chunks, size = [], 0
    while chunk := await file.read(256 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"The file exceeds the {get_settings().max_upload_mb} MB limit.")
        chunks.append(chunk)
    data = b"".join(chunks)
    try:
        f = validate_resume_upload(file.filename or "", data)
    except UploadRejected as exc:
        raise HTTPException(400, str(exc)) from exc
    resume = svc.create_resume(db, get_file_store(), p.user, f, name)
    db.refresh(resume)
    return _detail(resume)


@router.get("/resumes/{resume_id}", response_model=ResumeDetail)
def get_resume(resume_id: str, db: DB, p: CurrentPrincipal) -> ResumeDetail:
    return _detail(get_owned(db, Resume, resume_id, p.id))


@router.post("/resumes/{resume_id}/parse", response_model=ResumeDetail)
def reparse(resume_id: str, db: DB, p: Principal = Depends(web_only)) -> ResumeDetail:
    r = get_owned(db, Resume, resume_id, p.id)
    svc.parse_resume(db, get_file_store(), r)
    db.refresh(r)
    return _detail(r)


@router.post("/resumes/{resume_id}/primary", status_code=204)
def make_primary(resume_id: str, db: DB, p: Principal = Depends(web_only)) -> None:
    svc.set_primary(db, p.user, get_owned(db, Resume, resume_id, p.id))


@router.delete("/resumes/{resume_id}", status_code=204)
def delete_resume(resume_id: str, db: DB, p: Principal = Depends(web_only)) -> None:
    r = get_owned(db, Resume, resume_id, p.id)
    store = get_file_store()
    keys = [r.storage_key] + [v.storage_key for v in r.versions if v.storage_key]
    audit.record(db, user_id=p.id, actor="user", action="resume.deleted", entity_type="resume", entity_id=r.id)
    db.delete(r)
    db.commit()
    for k in keys:
        store.delete(k)


class ApplyToProfile(BaseModel):
    replace: bool = True


@router.post("/resumes/{resume_id}/apply-to-profile")
def apply_to_profile(resume_id: str, body: ApplyToProfile, db: DB, p: Principal = Depends(web_only)) -> dict:
    r = get_owned(db, Resume, resume_id, p.id)
    if r.parse_status != "parsed":
        raise HTTPException(400, "This resume hasn't been parsed successfully.")
    parsed = svc.parsed_from_resume(r, get_file_store())
    summary = profile_svc.apply_resume(db, p.user, r, parsed, replace=body.replace)
    db.commit()
    return summary


@router.get("/resumes/{resume_id}/download")
def download_original(resume_id: str, db: DB, p: CurrentPrincipal) -> Response:
    r = get_owned(db, Resume, resume_id, p.id)
    return Response(
        get_file_store().read(r.storage_key),
        media_type=r.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{svc.safe_download_name(r.name, "." + r.file_format)}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── Tailoring & versions ───────────────────────────────────────────────────────────────────────
class TailorIn(BaseModel):
    job_id: str


@router.post("/resumes/{resume_id}/tailor", response_model=VersionOut, status_code=201)
def tailor_resume(
    resume_id: str, body: TailorIn, db: DB, p: Principal = Depends(require_scope("resumes:write"))
) -> VersionOut:
    r = get_owned(db, Resume, resume_id, p.id)
    try:
        job = jobs_svc.assert_visible(db, p.user, body.job_id)
        v = svc.tailor(db, get_file_store(), p.user, r, job, actor=p.actor)
    except SourceError as exc:
        raise HTTPException(404, "Job not found.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _version(v)


@router.get("/resume-versions", response_model=list[VersionOut])
def list_versions(db: DB, p: CurrentPrincipal, job_id: str | None = None) -> list[VersionOut]:
    q = select(ResumeVersion).where(ResumeVersion.user_id == p.id).order_by(ResumeVersion.created_at.desc())
    if job_id:
        q = q.where(ResumeVersion.job_id == job_id)
    return [_version(v) for v in db.scalars(q)]


@router.get("/resume-versions/{version_id}", response_model=VersionOut)
def get_version(version_id: str, db: DB, p: CurrentPrincipal) -> VersionOut:
    return _version(get_owned(db, ResumeVersion, version_id, p.id))


class ChangeDecision(BaseModel):
    status: Literal["accepted", "rejected", "pending"]
    edited_text: str | None = Field(default=None, max_length=3000)
    acknowledge_flags: bool = False


@router.put("/resume-versions/{version_id}/changes/{change_id}")
def decide_change(
    version_id: str, change_id: str, body: ChangeDecision, db: DB, p: Principal = Depends(web_only)
) -> dict:
    v = get_owned(db, ResumeVersion, version_id, p.id)
    try:
        change = svc.update_change(
            v,
            change_id,
            status=body.status,
            edited_text=body.edited_text,
            acknowledge_flags=body.acknowledge_flags,
            evidence=v.resume.text_content or "",
        )
    except KeyError as exc:
        raise HTTPException(404, "Suggestion not found.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return change


@router.post("/resume-versions/{version_id}/approve", response_model=VersionOut)
def approve_version(version_id: str, db: DB, p: Principal = Depends(web_only)) -> VersionOut:
    v = get_owned(db, ResumeVersion, version_id, p.id)
    try:
        svc.approve_version(db, get_file_store(), p.user, v, actor="user")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _version(v)


@router.get("/resume-versions/{version_id}/download")
def download_version(version_id: str, db: DB, p: CurrentPrincipal) -> Response:
    v = get_owned(db, ResumeVersion, version_id, p.id)
    if v.status != "approved":
        raise HTTPException(400, "Approve this version before downloading it.")
    if v.storage_key:
        return Response(
            get_file_store().read(v.storage_key),
            media_type=DOCX_MIME,
            headers={"Content-Disposition": f'attachment; filename="{svc.safe_download_name(v.label, ".docx")}"'},
        )
    text = svc.tailored_text(v.resume, v)
    return Response(
        text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{svc.safe_download_name(v.label, ".txt")}"'},
    )
