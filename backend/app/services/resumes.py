from __future__ import annotations

import re
import secrets
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai import service as ai
from app.ai.guards import fabrication_flags, untrusted
from app.ai.provider import AIUnavailable, LLMError
from app.db import utcnow
from app.domain import docx_document, taxonomy
from app.domain.resume_parser import ParsedResume, parse_document
from app.models import Job, Resume, ResumeSection, ResumeVersion, User
from app.security.uploads import FileStore, ValidatedFile
from app.services import audit
from app.services import profile as profile_svc
from app.services import resume_review as review


def create_resume(db: Session, store: FileStore, user: User, f: ValidatedFile, name: str | None) -> Resume:
    existing = db.scalar(select(Resume).where(Resume.user_id == user.id, Resume.sha256 == f.sha256))
    if existing:
        return existing
    key = store.save(user.id, f.data, f.file_format)
    has_primary = db.scalar(select(Resume.id).where(Resume.user_id == user.id, Resume.is_primary.is_(True)))
    resume = Resume(
        user_id=user.id,
        name=(name or f.safe_name.rsplit(".", 1)[0])[:200],
        original_filename=f.safe_name,
        mime_type=f.mime_type,
        file_format=f.file_format,
        storage_key=key,
        sha256=f.sha256,
        size_bytes=len(f.data),
        is_primary=not has_primary,
    )
    db.add(resume)
    db.flush()
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="resume.uploaded",
        entity_type="resume",
        entity_id=resume.id,
        meta={"format": f.file_format, "size": len(f.data)},
    )
    parse_resume(db, store, resume)
    return resume


def parse_resume(db: Session, store: FileStore, resume: Resume) -> ParsedResume | None:
    try:
        parsed = parse_document(store.read(resume.storage_key), resume.file_format)
    except Exception as exc:  # noqa: BLE001 - any parser failure is reported to the user, not raised
        resume.parse_status, resume.parse_error = "failed", str(exc)[:500] or "The document could not be read."
        db.commit()
        return None
    resume.sections.clear()
    for i, s in enumerate(parsed.sections):
        resume.sections.append(
            ResumeSection(
                ordinal=i,
                raw_heading=s["raw_heading"],
                canonical_type=s["canonical_type"],
                confidence=s["confidence"],
                text=s["text"],
                paragraph_ids=s["paragraph_ids"],
            )
        )
    data = parsed.as_dict()
    resume.text_content = data.pop("text")
    resume.quality = data.pop("quality")
    data.pop("sections")
    resume.extracted = data
    resume.parse_status, resume.parse_error, resume.parsed_at, resume.parser = "parsed", None, utcnow(), "heuristic"
    db.commit()
    return parsed


def parsed_from_resume(resume: Resume, store: FileStore) -> ParsedResume:
    return parse_document(store.read(resume.storage_key), resume.file_format)


def set_primary(db: Session, user: User, resume: Resume) -> None:
    db.execute(update(Resume).where(Resume.user_id == user.id).values(is_primary=False))
    resume.is_primary = True
    db.commit()


# ── Tailoring ──────────────────────────────────────────────────────────────────────────────────
_EDITABLE = {"summary", "experience", "internships", "freelance", "projects", "academic_projects", "skills"}
_TAILOR_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "paragraph_id": {"type": "string"},
                    "proposed": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["paragraph_id", "proposed", "rationale"],
                "additionalProperties": False,
            },
        },
        "summary_of_fit": {"type": "string"},
    },
    "required": ["changes", "summary_of_fit"],
    "additionalProperties": False,
}


def _paragraph_map(db: Session, resume: Resume, store: FileStore) -> dict[str, dict[str, Any]]:
    """paragraph id → {text, section, is_list} for paragraphs in editable sections.

    Sections come from the user's corrected profile where it covers a paragraph (an entry moved from
    Experience to Education is treated as education), falling back to the detected structure.
    """
    section_of: dict[str, str] = {}
    for s in resume.sections:
        for pid in s.paragraph_ids:
            section_of[pid] = s.canonical_type
    section_of.update(review.corrected_section_map(db, resume))
    out: dict[str, dict[str, Any]] = {}
    if resume.file_format == "docx":
        for p in docx_document.read_paragraphs(store.read(resume.storage_key)):
            sec = section_of.get(p.id)
            if sec in _EDITABLE:
                out[p.id] = {"text": p.text, "section": sec, "is_list": p.is_list}
    else:
        lines = (resume.text_content or "").splitlines()
        for i, t in enumerate(lines):
            sec = section_of.get(f"l{i}")
            if sec in _EDITABLE:
                out[f"l{i}"] = {"text": t, "section": sec, "is_list": t.lstrip()[:1] in "•-*–"}
    return out


def _reorder_skills(text: str, priority: list[str]) -> str | None:
    """Reorder a comma-separated skills line so job-relevant skills come first. Pure reordering."""
    label, sep, items_text = text.partition(":") if ":" in text[:40] else ("", "", text)
    items = [i.strip() for i in items_text.split(",")]
    if len(items) < 3 or any(not i for i in items):
        return None
    rank = {s: i for i, s in enumerate(priority)}

    def key(item: str) -> tuple[int, int]:
        canon = taxonomy.canonicalize(item)
        return (rank.get(canon, len(rank)) if canon else len(rank), items.index(item))

    reordered = sorted(items, key=key)
    if reordered == items:
        return None
    body = ", ".join(reordered)
    return f"{label}{sep} {body}" if sep else body


def analyse_fit(have: set[str], job: Job) -> dict[str, Any]:
    """Skill fit against the job, from the user's corrected profile skills."""
    req, pref = job.required_skills or [], job.preferred_skills or []
    return {
        "matched_required": [s for s in req if s in have],
        "missing_required": [s for s in req if s not in have],
        "matched_preferred": [s for s in pref if s in have],
        "missing_preferred": [s for s in pref if s not in have],
        "note": "Missing skills are never added to your resume. If you have one, add it to your profile and "
        "resume yourself so it's backed by real experience.",
    }


def tailor(db: Session, store: FileStore, user: User, resume: Resume, job: Job, *, actor: str) -> ResumeVersion:
    if resume.parse_status != "parsed":
        raise ValueError("Parse this resume before tailoring it.")
    paras = _paragraph_map(db, resume, store)
    analysis = analyse_fit(set(profile_svc.candidate_profile(db, user).skills), job)
    priority = analysis["matched_required"] + analysis["matched_preferred"]
    changes: list[dict[str, Any]] = []

    def add(pid: str, proposed: str, rationale: str, kind: str, flags: list[str]) -> None:
        changes.append(
            {
                "id": secrets.token_hex(6),
                "paragraph_id": pid,
                "section": paras[pid]["section"],
                "original": paras[pid]["text"],
                "proposed": proposed,
                "rationale": rationale,
                "kind": kind,
                "flags": flags,
                "status": "pending",
            }
        )

    for pid, p in paras.items():
        if p["section"] == "skills" and priority:
            new = _reorder_skills(p["text"], priority)
            if new:
                add(
                    pid, new, "Moves skills this job asks for to the front. Nothing is added or removed.", "reorder", []
                )

    generated_by = "deterministic"
    ai_note = None
    if ai.status()["configured"]:
        try:
            corrected = review.profile_text(db, user)
            evidence = f"{resume.text_content or ''}\n{corrected}"
            candidates = {pid: p for pid, p in paras.items() if p["section"] != "skills"}
            listing = "\n".join(f"[{pid}] ({p['section']}) {p['text']}" for pid, p in list(candidates.items())[:80])
            prompt = (
                "Tailor this resume to the job by rewording existing paragraphs. Rules:\n"
                "- Only rewrite paragraph ids listed below; keep each change to one paragraph.\n"
                "- Reword, reorder emphasis and surface relevant keywords ONLY where the candidate's own text "
                "already supports them. Do not add skills, tools, numbers, employers or outcomes that are not "
                "already in the resume.\n- Keep roughly the same length so the layout is preserved.\n"
                "- Propose at most 8 changes, only where they clearly improve relevance.\n\n"
                f"Resume paragraphs:\n{untrusted('resume', listing)}\n\n"
                "The candidate has reviewed and corrected this structured profile; where it disagrees with the "
                "paragraph labels above, trust the profile:\n"
                f"{untrusted('reviewed_profile', corrected, 8000)}\n\n"
                f"Job: {job.title} at {job.company.name}\n{untrusted('job_description', job.description_text, 12000)}"
            )
            result = ai.complete(
                db,
                user,
                feature="resume_tailoring",
                system="You are an expert, scrupulously honest resume editor.",
                messages=[{"role": "user", "content": prompt}],
                json_schema=_TAILOR_SCHEMA,
            )
            generated_by = f"llm:{result.model}"
            for c in (result.data or {}).get("changes", [])[:8]:
                pid = c.get("paragraph_id")
                if (
                    pid not in candidates
                    or not c.get("proposed")
                    or c["proposed"].strip() == paras[pid]["text"].strip()
                ):
                    continue
                proposed = c["proposed"].strip()
                add(
                    pid,
                    proposed,
                    c.get("rationale", "")[:500],
                    "rewrite",
                    fabrication_flags(proposed, paras[pid]["text"], evidence),
                )
            analysis["summary_of_fit"] = (result.data or {}).get("summary_of_fit")
        except AIUnavailable:
            ai_note = "AI is not configured — only factual reordering suggestions were generated."
        except LLMError as exc:
            ai_note = f"AI rewriting was skipped: {exc}"
    else:
        ai_note = "Connect an AI provider in Settings to get wording suggestions. Reordering suggestions are below."
    analysis["ai_note"] = ai_note

    version = ResumeVersion(
        resume_id=resume.id,
        user_id=user.id,
        job_id=job.id,
        label=f"{job.title} — {job.company.name}"[:200],
        changes=changes,
        analysis=analysis,
        generated_by=generated_by,
    )
    db.add(version)
    db.flush()
    audit.record(
        db,
        user_id=user.id,
        actor=actor,
        action="resume.tailored",
        entity_type="resume_version",
        entity_id=version.id,
        meta={"job_id": job.id, "changes": len(changes), "generated_by": generated_by},
    )
    db.commit()
    return version


def update_change(
    version: ResumeVersion,
    change_id: str,
    *,
    status: str,
    edited_text: str | None,
    acknowledge_flags: bool,
    evidence: str,
) -> dict[str, Any]:
    if version.status == "approved":
        raise ValueError("This version is approved. Create a new tailored version to make changes.")
    changes = [dict(c) for c in version.changes]
    change = next((c for c in changes if c["id"] == change_id), None)
    if change is None:
        raise KeyError(change_id)
    if edited_text is not None:
        change["proposed"] = edited_text.strip()
        change["kind"] = "user_edit"
        change["flags"] = fabrication_flags(change["proposed"], change["original"], evidence)
    if status == "accepted" and change["flags"] and not acknowledge_flags:
        raise ValueError("This change introduces content not found in your resume. Confirm it's accurate first.")
    change["status"] = status
    change["acknowledged"] = bool(acknowledge_flags and change["flags"])
    version.changes = changes  # reassign so the JSON column is marked dirty
    return change


def approve_version(db: Session, store: FileStore, user: User, version: ResumeVersion, *, actor: str) -> ResumeVersion:
    if any(c["status"] == "pending" for c in version.changes):
        raise ValueError("Accept or reject every suggestion before approving.")
    resume = version.resume
    accepted = {c["paragraph_id"]: c["proposed"] for c in version.changes if c["status"] == "accepted"}
    if resume.file_format == "docx":
        data = docx_document.apply_edits(store.read(resume.storage_key), accepted)
        version.storage_key = store.save(user.id, data, "docx")
    version.status, version.approved_at = "approved", utcnow()
    audit.record(
        db,
        user_id=user.id,
        actor=actor,
        action="resume_version.approved",
        entity_type="resume_version",
        entity_id=version.id,
        meta={"accepted": len(accepted)},
    )
    db.commit()
    return version


def tailored_text(resume: Resume, version: ResumeVersion) -> str:
    """Plain-text rendering of a version (used for PDF resumes, which can't be edited in place)."""
    lines = (resume.text_content or "").splitlines()
    accepted = {c["paragraph_id"]: c["proposed"] for c in version.changes if c["status"] == "accepted"}
    return "\n".join(accepted.get(f"l{i}", line) for i, line in enumerate(lines))


def safe_download_name(name: str, suffix: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "", name).strip()[:80] + suffix
