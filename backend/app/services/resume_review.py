"""Resume Review & Editor: the corrected, structured profile built from a resume.

The uploaded file and the raw extraction (`resume.extracted`, `resume.sections`) are never changed here.
Corrections live in the profile tables (experiences, projects, education, certifications, skills and
`profile.summary`). Those tables are what matching, tailoring and application answers read, so a
correction takes effect everywhere as soon as it's saved.

Every extracted entry carries a fingerprint of its source text. A re-extraction uses it to recognise
entries it has seen before:
* entries the user edited, moved, split or merged are kept exactly as the user left them;
* entries the user deleted (or merged away) stay deleted;
* new and changed entries are shown in a preview, and are only applied when the user asks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import taxonomy
from app.domain.resume_parser import (
    REVIEW_THRESHOLD,
    Line,
    ParsedResume,
    Section,
    document_lines,
    fingerprint,
    parse_certifications,
    parse_document,
    parse_education,
    parse_experience,
    parse_projects,
)
from app.models import Certification, Education, Experience, Project, Resume, Skill, User
from app.security.uploads import FileStore
from app.services import audit

KINDS = ("experience", "projects", "education", "certifications")
MODELS: dict[str, Any] = {
    "experience": Experience,
    "projects": Project,
    "education": Education,
    "certifications": Certification,
}
FIELDS: dict[str, tuple[str, ...]] = {
    "experience": ("category", "employer", "title", "location", "start_date", "end_date", "is_current", "bullets"),
    "projects": ("category", "name", "description", "technologies", "url"),
    "education": ("institution", "degree", "field", "start_date", "end_date", "grade", "details"),
    "certifications": ("name", "issuer", "issued_on", "details"),
}
MOVE_TARGETS = (*KINDS, "summary", "skills")
_REVIEW = ("source_ids", "confidence", "review_reasons", "needs_review", "user_edited", "fingerprint")


class ReviewError(ValueError):
    """A correction that can't be applied (shown to the user as a 400)."""


# ── Helpers ────────────────────────────────────────────────────────────────────────────────────
def kind_of(row: Any) -> str:
    return next(k for k, m in MODELS.items() if isinstance(row, m))


def entries(db: Session, resume: Resume) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for kind, model in MODELS.items():
        rows = db.scalars(
            select(model)
            .where(model.user_id == resume.user_id, model.resume_id == resume.id)
            .order_by(model.created_at)
        )
        out += [(kind, r) for r in rows]
    return out


def label(kind: str, f: dict[str, Any] | Any) -> str:
    g = (lambda k: f.get(k)) if isinstance(f, dict) else (lambda k: getattr(f, k, None))
    if kind == "experience":
        return " · ".join(x for x in (g("title"), g("employer")) if x) or "Untitled role"
    if kind == "education":
        return " · ".join(x for x in (g("degree"), g("institution")) if x) or "Untitled education"
    return g("name") or "Untitled"


def _fields(row: Any) -> dict[str, Any]:
    return {k: getattr(row, k) for k in FIELDS[kind_of(row)]}


def _review_state(resume: Resume) -> dict[str, Any]:
    return dict(resume.review or {})


def _save_review(resume: Resume, state: dict[str, Any]) -> None:
    resume.review = state  # reassign so the JSON column is marked dirty


def _dismiss(resume: Resume, *fps: str | None) -> None:
    state = _review_state(resume)
    dismissed = list(state.get("dismissed", []))
    for fp in fps:
        if fp and fp not in dismissed:
            dismissed.append(fp)
    state["dismissed"] = dismissed
    _save_review(resume, state)


def mark_edited(row: Any) -> None:
    """A user correction: re-extraction will never overwrite this entry."""
    row.user_edited, row.needs_review, row.review_reasons = True, False, []


def record_deletion(db: Session, row: Any) -> None:
    """Remember that the user deleted an extracted entry, so re-extraction doesn't bring it back."""
    if row.resume_id and row.fingerprint:
        resume = db.get(Resume, row.resume_id)
        if resume is not None:
            _dismiss(resume, row.fingerprint)


def record_skill_deletion(db: Session, user_id: str, name: str) -> None:
    for resume in db.scalars(select(Resume).where(Resume.user_id == user_id)):
        state = _review_state(resume)
        names = list(state.get("dismissed_skills", []))
        if name not in names:
            state["dismissed_skills"] = [*names, name]
            _save_review(resume, state)


def _lines(resume: Resume, store: FileStore) -> dict[str, Line]:
    return {ln.id: ln for ln in document_lines(store.read(resume.storage_key), resume.file_format)}


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


# ── Candidates from an extraction ─────────────────────────────────────────────────────────────
@dataclass
class Candidate:
    kind: str
    fields: dict[str, Any]
    source_ids: list[str]
    confidence: float
    reasons: list[str]
    fingerprint: str

    @property
    def needs_review(self) -> bool:
        return self.confidence < REVIEW_THRESHOLD


def candidates(parsed: ParsedResume, text_by_id: dict[str, str]) -> list[Candidate]:
    out: list[Candidate] = []

    def add(kind: str, fields: dict[str, Any], ids: list[str], conf: float, reasons: list[str]) -> None:
        fp = fingerprint([text_by_id.get(i, "") for i in ids] or [label(kind, fields)])
        out.append(Candidate(kind, fields, list(ids), conf, list(reasons), fp))

    for e in parsed.experiences:
        f = {
            "category": e.category,
            "employer": e.employer or "Untitled role",
            "title": e.title,
            "location": e.location,
            "start_date": _d(e.start_date),
            "end_date": _d(e.end_date),
            "is_current": e.is_current,
            "bullets": e.bullets[:30],
        }
        add("experience", f, e.source_ids, e.confidence, e.review_reasons)
    for p in parsed.projects:
        f = {
            "category": p.category,
            "name": p.name,
            "description": p.description[:4000],
            "technologies": p.technologies[:40],
            "url": p.url,
        }
        add("projects", f, p.source_ids, p.confidence, p.review_reasons)
    for ed in parsed.education:
        f = {
            "institution": ed.institution or "Untitled education",
            "degree": ed.degree,
            "field": ed.field,
            "start_date": _d(ed.start_date),
            "end_date": _d(ed.end_date),
            "grade": ed.grade,
            "details": [],
        }
        add("education", f, ed.source_ids, ed.confidence, ed.review_reasons)
    for c in parsed.certifications:
        f = {"name": c["name"], "issuer": c.get("issuer"), "issued_on": None, "details": []}
        add("certifications", f, [c["source_id"]], c.get("confidence", 1.0), c.get("review_reasons", []))
    return out


def _new_row(user: User, resume: Resume, c: Candidate) -> Any:
    return MODELS[c.kind](
        user_id=user.id,
        resume_id=resume.id,
        **c.fields,
        source_ids=c.source_ids,
        confidence=c.confidence,
        review_reasons=c.reasons if c.needs_review else [],
        needs_review=c.needs_review,
        user_edited=False,
        fingerprint=c.fingerprint,
        verified=False,
    )


def _legacy_key(kind: str, f: dict[str, Any] | Any) -> str:
    return re.sub(r"\W+", " ", label(kind, f)).strip().lower()


# ── Re-extraction plan ─────────────────────────────────────────────────────────────────────────
def plan(db: Session, user: User, resume: Resume, parsed: ParsedResume, text_by_id: dict[str, str]) -> dict[str, Any]:
    """Compare a fresh extraction with the corrected profile. Read-only."""
    state = _review_state(resume)
    dismissed = set(state.get("dismissed", []))
    current = entries(db, resume)
    by_fp = {row.fingerprint: (kind, row) for kind, row in current if row.fingerprint}
    # Rows imported before fingerprints existed are matched by kind + label, once.
    legacy = {
        (kind, _legacy_key(kind, row)): row
        for kind, row in current
        if not row.fingerprint and not row.user_edited and not row.source_ids
    }
    result: dict[str, list[Any]] = {k: [] for k in ("add", "update", "unchanged", "kept", "dismissed", "stale")}
    seen: set[str] = set()
    for c in candidates(parsed, text_by_id):
        if c.fingerprint in seen:
            continue
        seen.add(c.fingerprint)
        if c.fingerprint in dismissed:
            result["dismissed"].append(c)
            continue
        hit = by_fp.get(c.fingerprint)
        if hit is None and (row := legacy.pop((c.kind, _legacy_key(c.kind, c.fields)), None)) is not None:
            hit = (c.kind, row)
        if hit is None:
            result["add"].append(c)
            continue
        kind, row = hit
        if row.user_edited:
            result["kept"].append((c, kind, row))
        elif kind == c.kind and _fields(row) == c.fields and row.fingerprint == c.fingerprint:
            result["unchanged"].append((c, kind, row))
        else:
            result["update"].append((c, kind, row))
    result["stale"] = [
        (kind, row) for kind, row in current if row.fingerprint and row.fingerprint not in seen and not row.user_edited
    ]
    have = {s.name for s in db.scalars(select(Skill).where(Skill.user_id == user.id))}
    dismissed_skills = set(state.get("dismissed_skills", []))
    new_skills = {
        n: ev
        for n, ev in parsed.skills.items()
        if taxonomy.normalize_skill(n) not in have and taxonomy.normalize_skill(n) not in dismissed_skills
    }
    summary = None
    extracted = (parsed.summary or "").strip()
    current_summary = (user.profile.summary or "").strip()
    if extracted and not current_summary and not state.get("summary_dismissed"):
        summary = {"action": "fill", "extracted": extracted}
    elif extracted and current_summary and _norm(extracted) != _norm(current_summary):
        summary = {"action": "differs", "extracted": extracted, "current": current_summary}
    return {**result, "skills": new_skills, "summary": summary}


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def describe(p: dict[str, Any]) -> dict[str, Any]:
    """JSON view of a plan for the preview dialog."""

    def cand(c: Candidate) -> dict[str, Any]:
        return {
            "kind": c.kind,
            "label": label(c.kind, c.fields),
            "confidence": c.confidence,
            "review_reasons": c.reasons,
        }

    def pair(x: tuple[Candidate, str, Any]) -> dict[str, Any]:
        c, kind, row = x
        return {
            **cand(c),
            "entry_id": row.id,
            "current_kind": kind,
            "current_label": label(kind, row),
            "differs": kind != c.kind or _fields(row) != c.fields,
        }

    return {
        "add": [cand(c) for c in p["add"]],
        "update": [pair(x) for x in p["update"]],
        "unchanged": len(p["unchanged"]),
        "kept": [pair(x) for x in p["kept"]],
        "dismissed": [cand(c) for c in p["dismissed"]],
        "stale": [{"kind": k, "entry_id": r.id, "label": label(k, r)} for k, r in p["stale"]],
        "skills": sorted(p["skills"]),
        "summary": p["summary"],
    }


def apply_plan(
    db: Session, user: User, resume: Resume, p: dict[str, Any], *, remove_stale: bool, accept_summary: bool = False
) -> dict[str, int]:
    from app.services.profile import upsert_skill  # profile imports this module

    for c in p["add"]:
        db.add(_new_row(user, resume, c))
    for c, kind, row in p["update"]:
        if kind != c.kind:
            db.delete(row)
            db.add(_new_row(user, resume, c))
        else:
            for k, v in c.fields.items():
                setattr(row, k, v)
            row.source_ids, row.fingerprint, row.confidence = c.source_ids, c.fingerprint, c.confidence
            row.needs_review, row.review_reasons = c.needs_review, (c.reasons if c.needs_review else [])
    stale_note = "The latest extraction no longer finds this entry. Keep it, edit it or delete it."
    for _kind, row in p["stale"]:
        if remove_stale:
            db.delete(row)
        else:
            row.needs_review, row.review_reasons = True, [stale_note]
    for name, evidence in p["skills"].items():
        upsert_skill(db, user.id, name, source="resume", verified=False, evidence=evidence)
    summary = p["summary"]
    if summary and (summary["action"] == "fill" or (summary["action"] == "differs" and accept_summary)):
        user.profile.summary = summary["extracted"][:4000]
    state = _review_state(resume)
    state["last_reextracted_at"] = utcnow().isoformat()
    _save_review(resume, state)
    return {
        "added": len(p["add"]),
        "updated": len(p["update"]),
        "kept_corrections": len(p["kept"]),
        "still_deleted": len(p["dismissed"]),
        "stale": len(p["stale"]),
        "skills_added": len(p["skills"]),
    }


def _extract(resume: Resume, store: FileStore) -> tuple[ParsedResume, dict[str, str]]:
    data = store.read(resume.storage_key)
    parsed = parse_document(data, resume.file_format)
    return parsed, {ln.id: ln.text for ln in document_lines(data, resume.file_format)}


def import_resume(
    db: Session, store: FileStore, user: User, resume: Resume, *, replace: bool, parsed: ParsedResume | None = None
) -> dict[str, Any]:
    """Build (or refresh) the corrected profile from a resume. Corrections are always preserved.

    `replace` also removes unedited entries the latest extraction no longer finds. Contact fields are
    only filled when empty, as before.
    """
    fresh, text_by_id = _extract(resume, store)
    parsed = parsed or fresh
    p = user.profile
    c = parsed.contact
    filled: list[str] = []
    for fld, value in (
        ("full_name", c.get("name")),
        ("phone", c.get("phone")),
        ("location", c.get("location")),
        ("linkedin_url", c.get("linkedin")),
    ):
        if value and not getattr(p, fld):
            if fld == "linkedin_url" and not value.startswith("http"):
                value = "https://" + value
            setattr(p, fld, value)
            filled.append(fld)
    if p.years_experience is None:
        p.years_experience = parsed.years_experience
        filled.append("years_experience")
    had_summary = bool(p.summary)
    the_plan = plan(db, user, resume, parsed, text_by_id)
    changes = apply_plan(db, user, resume, the_plan, remove_stale=replace)
    if not had_summary and p.summary:
        filled.append("summary")
    state = _review_state(resume)
    state.setdefault("imported_at", utcnow().isoformat())
    state["summary_source_ids"] = parsed.summary_source_ids
    _save_review(resume, state)
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="profile.prefilled_from_resume",
        entity_type="resume",
        entity_id=resume.id,
        meta={"experiences": len(parsed.experiences), "skills": len(parsed.skills), **changes},
    )
    return {
        "filled_fields": filled,
        "experiences": len(parsed.experiences),
        "projects": len(parsed.projects),
        "education": len(parsed.education),
        "skills": len(parsed.skills),
        "certifications": len(parsed.certifications),
        "changes": changes,
    }


def reextract_preview(db: Session, store: FileStore, user: User, resume: Resume) -> dict[str, Any]:
    parsed, text_by_id = _extract(resume, store)
    return describe(plan(db, user, resume, parsed, text_by_id))


def reextract_apply(
    db: Session, store: FileStore, user: User, resume: Resume, *, remove_stale: bool, accept_summary: bool
) -> dict[str, Any]:
    from app.services.resumes import parse_resume  # resumes imports this module

    if parse_resume(db, store, resume) is None:  # refreshes the raw extraction; commits
        raise ReviewError(resume.parse_error or "The document could not be read.")
    parsed, text_by_id = _extract(resume, store)
    changes = apply_plan(
        db,
        user,
        resume,
        plan(db, user, resume, parsed, text_by_id),
        remove_stale=remove_stale,
        accept_summary=accept_summary,
    )
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="resume.reextracted",
        entity_type="resume",
        entity_id=resume.id,
        meta=changes,
    )
    return changes


# ── Editing ────────────────────────────────────────────────────────────────────────────────────
def create_entry(db: Session, user: User, resume: Resume, kind: str, data: dict[str, Any]) -> Any:
    row = MODELS[kind](user_id=user.id, resume_id=resume.id, **data)
    mark_edited(row)
    db.add(row)
    db.flush()
    return row


def update_entry(row: Any, data: dict[str, Any]) -> Any:
    for k, v in data.items():
        setattr(row, k, v)
    mark_edited(row)
    return row


def delete_entry(db: Session, resume: Resume, row: Any) -> None:
    _dismiss(resume, row.fingerprint)
    db.delete(row)


def _parse_as(kind: str, lines: list[Line], category: str | None) -> list[dict[str, Any]]:
    """Run the target section's parser over the entry's original lines."""
    if not lines:
        return []
    sec = Section(kind, None, 1.0, lines)
    if kind == "experience":
        return [
            {
                "category": category or e.category,
                "employer": e.employer or "Untitled role",
                "title": e.title,
                "location": e.location,
                "start_date": _d(e.start_date),
                "end_date": _d(e.end_date),
                "is_current": e.is_current,
                "bullets": e.bullets[:30],
            }
            for e in parse_experience(sec, category or "professional")
        ]
    if kind == "projects":
        return [
            {
                "category": category or p.category,
                "name": p.name,
                "description": p.description[:4000],
                "technologies": p.technologies[:40],
                "url": p.url,
            }
            for p in parse_projects(sec, category or "personal")
        ]
    if kind == "education":
        out = []
        for e in parse_education(sec):
            # Lines that aren't the institution/degree line are kept as details, so nothing is lost.
            core = [x for x in (e.institution, e.degree, e.grade) if x]
            rest = [ln.text for ln in lines if not any(c and c in ln.text for c in core)]
            out.append(
                {
                    "institution": e.institution or "Untitled education",
                    "degree": e.degree,
                    "field": e.field,
                    "start_date": _d(e.start_date),
                    "end_date": _d(e.end_date),
                    "grade": e.grade,
                    "details": rest[:30],
                }
            )
        return out
    if kind == "certifications":
        return [
            {"name": c["name"], "issuer": c.get("issuer"), "issued_on": None, "details": []}
            for c in parse_certifications(lines)
        ]
    return []


def _parts(kind: str, row: Any) -> tuple[str, str | None, list[str]]:
    """(heading, subheading, body lines) of an entry, for converting it to another kind."""
    if kind == "experience":
        return row.employer, row.title, list(row.bullets)
    if kind == "projects":
        return row.name, None, [x for x in (row.description or "").splitlines() if x.strip()]
    if kind == "education":
        sub = " in ".join(x for x in (row.degree, row.field) if x) or None
        return row.institution, sub, [*([f"Grade: {row.grade}"] if row.grade else []), *row.details]
    return row.name, row.issuer, list(row.details)


def build_fields(
    to: str,
    head: str,
    sub: str | None,
    body: list[str],
    *,
    category: str | None = None,
    start: date | None = None,
    end: date | None = None,
    is_current: bool = False,
    technologies: list[str] | None = None,
    url: str | None = None,
    grade: str | None = None,
) -> dict[str, Any]:
    """Fields for a `to` entry built from a heading, a subheading and body lines (no text is dropped)."""
    head = (head or "").strip() or "Untitled"
    text = " ".join([head, sub or "", *body])
    if to == "experience":
        return {
            "category": category or "professional",
            "employer": head[:200],
            "title": (sub or None) and sub[:200],
            "location": None,
            "start_date": start,
            "end_date": None if is_current else end,
            "is_current": is_current,
            "bullets": body[:30],
        }
    if to == "projects":
        found = re.search(r"https?://\S+|github\.com/\S+", text)
        return {
            "category": category or "personal",
            "name": head[:200],
            "description": "\n".join([*([sub] if sub else []), *body])[:4000] or None,
            "technologies": list(dict.fromkeys([*(technologies or []), *taxonomy.extract_skills(text)]))[:40],
            "url": found.group(0)[:500] if found else url,
        }
    if to == "education":
        return {
            "institution": head[:300],
            "degree": (sub or None) and sub[:200],
            "field": None,
            "start_date": start,
            "end_date": end,
            "grade": grade,
            "details": body[:30],
        }
    return {"name": head[:300], "issuer": (sub or None) and sub[:200], "issued_on": None, "details": body[:30]}


def _map_fields(kind: str, row: Any, to: str, category: str | None) -> dict[str, Any]:
    head, sub, body = _parts(kind, row)
    return build_fields(
        to,
        head,
        sub,
        body,
        category=category,
        start=getattr(row, "start_date", None),
        end=getattr(row, "end_date", None),
        is_current=bool(getattr(row, "is_current", False)),
        technologies=list(getattr(row, "technologies", [])),
        url=getattr(row, "url", None),
        grade=getattr(row, "grade", None),
    )


def move_entry(
    db: Session, store: FileStore, user: User, resume: Resume, row: Any, to: str, *, category: str | None = None
) -> dict[str, Any]:
    """Move an entry to another section. Returns {"entries": [(kind, row)...], "summary": str | None, "skills": [...]}."""
    kind = kind_of(row)
    if to not in MOVE_TARGETS:
        raise ReviewError("Unknown section.")
    if to == kind and category in (None, getattr(row, "category", None)):
        raise ReviewError("The entry is already in that section.")
    if to == kind:  # category change within the same section, e.g. professional → internship
        row.category = category
        mark_edited(row)
        db.flush()
        return {"entries": [(kind, row)], "summary": None, "skills": []}
    head, sub, body = _parts(kind, row)
    text_lines = [x for x in (head, sub, *body) if x]
    if to == "summary":
        addition = "\n".join(text_lines)
        p = user.profile
        p.summary = f"{p.summary}\n{addition}".strip()[:4000] if p.summary else addition[:4000]
        state = _review_state(resume)
        state["summary_source_ids"] = [*state.get("summary_source_ids", []), *row.source_ids]
        state["summary_edited"] = True
        _save_review(resume, state)
        delete_entry(db, resume, row)
        return {"entries": [], "summary": p.summary, "skills": []}
    if to == "skills":
        from app.services.profile import upsert_skill

        found = taxonomy.extract_skills(" ".join(text_lines))
        for name in found:
            upsert_skill(db, user.id, name, source="user", verified=True, evidence=[" ".join(text_lines)[:200]])
        delete_entry(db, resume, row)
        return {"entries": [], "summary": None, "skills": sorted(found)}

    new_fields: list[dict[str, Any]] = []
    if not row.user_edited and row.source_ids:
        # Untouched extraction: re-read the original lines with the target section's parser.
        lines = _lines(resume, store)
        src = [lines[i] for i in row.source_ids if i in lines]
        if len(src) == len(row.source_ids):
            new_fields = _parse_as(to, src, category)
    if not new_fields:
        # Edited by the user (or no source lines): carry the user's text across field by field.
        new_fields = [_map_fields(kind, row, to, category)]
    created = []
    for i, f in enumerate(new_fields):
        new = MODELS[to](
            user_id=user.id,
            resume_id=resume.id,
            **f,
            source_ids=row.source_ids if i == 0 else [],
            confidence=row.confidence,
            fingerprint=row.fingerprint if i == 0 else None,
            verified=row.verified,
        )
        mark_edited(new)
        db.add(new)
        created.append((to, new))
    db.delete(row)
    db.flush()
    return {"entries": created, "summary": None, "skills": []}


_LIST_FIELD = {"experience": "bullets", "education": "details", "certifications": "details"}


def _body(kind: str, row: Any) -> list[str]:
    if kind == "projects":
        return [x for x in (row.description or "").splitlines() if x.strip()]
    return list(getattr(row, _LIST_FIELD[kind]))


def _set_body(kind: str, row: Any, body: list[str]) -> None:
    if kind == "projects":
        row.description = "\n".join(body) or None
    else:
        setattr(row, _LIST_FIELD[kind], body)


def split_entry(db: Session, user: User, resume: Resume, row: Any, at: int) -> tuple[Any, Any]:
    """Split an entry before body line `at`. That line becomes the heading of the new entry, which is how
    two roles (or projects) end up merged when the second one's heading wasn't recognised."""
    kind = kind_of(row)
    body = _body(kind, row)
    if not 0 <= at < len(body):
        raise ReviewError("Choose a line to split at.")
    keep, rest = body[:at], body[at:]
    header, tail = (
        Line("s0", rest[0], is_bold=True),
        [Line(f"s{i + 1}", t, is_list=True) for i, t in enumerate(rest[1:])],
    )
    category = getattr(row, "category", None)
    parsed = _parse_as(kind, [header, *tail], category)
    fields = parsed[0] if len(parsed) == 1 else build_fields(kind, rest[0], None, rest[1:], category=category)
    _set_body(kind, row, keep)
    mark_edited(row)
    new = MODELS[kind](user_id=user.id, resume_id=resume.id, **fields, verified=row.verified)
    mark_edited(new)
    db.add(new)
    db.flush()
    return row, new


def merge_entries(db: Session, resume: Resume, rows: list[Any]) -> Any:
    kinds = {kind_of(r) for r in rows}
    if len(rows) < 2 or len(kinds) != 1:
        raise ReviewError("Choose two or more entries from the same section to merge.")
    kind = kinds.pop()
    first, others = rows[0], rows[1:]
    body = _body(kind, first)
    for r in others:
        for k in FIELDS[kind]:
            if getattr(first, k) in (None, "", []) and getattr(r, k) not in (None, "", []):
                setattr(first, k, getattr(r, k))
        body += [x for x in _body(kind, r) if x not in body]
        if kind in ("experience", "education"):
            if r.start_date and (not first.start_date or r.start_date < first.start_date):
                first.start_date = r.start_date
            if kind == "experience" and r.is_current:
                first.is_current, first.end_date = True, None
            elif (
                r.end_date
                and not getattr(first, "is_current", False)
                and (not first.end_date or r.end_date > first.end_date)
            ):
                first.end_date = r.end_date
        if kind == "projects":
            first.technologies = list(dict.fromkeys([*first.technologies, *r.technologies]))
        first.source_ids = [*first.source_ids, *(i for i in r.source_ids if i not in first.source_ids)]
        first.verified = first.verified and r.verified
    _set_body(kind, first, body[:30] if kind != "projects" else body)
    _dismiss(resume, *(r.fingerprint for r in others))
    for r in others:
        db.delete(r)
    mark_edited(first)
    db.flush()
    return first


def create_from_source(
    db: Session, store: FileStore, user: User, resume: Resume, kind: str, source_ids: list[str]
) -> list[Any]:
    """Assign unclassified content (by its source lines) to a section."""
    lines = _lines(resume, store)
    src = [lines[i] for i in source_ids if i in lines]
    if not src:
        raise ReviewError("That content wasn't found in the resume.")
    if kind == "summary":
        text = "\n".join(ln.text for ln in src)
        p = user.profile
        p.summary = f"{p.summary}\n{text}".strip()[:4000] if p.summary else text[:4000]
        made: list[Any] = []
    else:
        made = []
        fields = _parse_as(kind, src, None) or [build_fields(kind, src[0].text, None, [ln.text for ln in src[1:]])]
        for i, f in enumerate(fields):
            row = MODELS[kind](
                user_id=user.id,
                resume_id=resume.id,
                **f,
                source_ids=source_ids if i == 0 else [],
                fingerprint=fingerprint([ln.text for ln in src]) if i == 0 else None,
                verified=True,
            )
            mark_edited(row)
            db.add(row)
            made.append(row)
    state = _review_state(resume)
    state["assigned_unclassified"] = [*state.get("assigned_unclassified", []), *source_ids]
    _save_review(resume, state)
    db.flush()
    return made


def set_summary(user: User, resume: Resume, text: str | None) -> None:
    value = (text or "").strip()
    user.profile.summary = value[:4000] or None
    state = _review_state(resume)
    state["summary_edited"] = True
    state["summary_dismissed"] = not value
    _save_review(resume, state)


def confirm(db: Session, user: User, resume: Resume) -> int:
    n = 0
    for _kind, row in entries(db, resume):
        n += 1
        row.needs_review, row.review_reasons, row.verified = False, [], True
    state = _review_state(resume)
    state["reviewed_at"] = utcnow().isoformat()
    state["summary_edited"] = True
    _save_review(resume, state)
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="resume.review_confirmed",
        entity_type="resume",
        entity_id=resume.id,
        meta={"entries": n},
    )
    return n


# ── Views ──────────────────────────────────────────────────────────────────────────────────────
def issues(kind: str, row: Any) -> list[str]:
    """Validation hints shown next to an entry (not errors: the entry is saved either way)."""
    out: list[str] = []
    if kind == "experience":
        if not row.title:
            out.append("Add the job title.")
        if not row.start_date:
            out.append("Add the start date.")
        if not row.is_current and row.start_date and not row.end_date:
            out.append("Add an end date, or mark it as current.")
        if not row.bullets:
            out.append("Describe what you did.")
    elif kind == "education":
        if not row.degree:
            out.append("Add the degree or qualification.")
        if not row.end_date:
            out.append("Add the graduation date.")
    elif kind == "projects" and not row.description:
        out.append("Describe the project.")
    if (row.employer if kind == "experience" else getattr(row, "name", None) or getattr(row, "institution", "")) in (
        "Untitled",
        "Untitled role",
        "Untitled education",
    ):
        out.append("Give this entry a name.")
    return out


def corrected_section_map(db: Session, resume: Resume) -> dict[str, str]:
    """Paragraph id → section, as corrected by the user (used by tailoring)."""
    out: dict[str, str] = {}
    for kind, row in entries(db, resume):
        if kind == "experience":
            sec = {"internship": "internships", "freelance": "freelance"}.get(row.category, "experience")
        elif kind == "projects":
            sec = "academic_projects" if row.category == "academic" else "projects"
        else:
            sec = kind
        for pid in row.source_ids:
            out[pid] = sec
    for pid in _review_state(resume).get("summary_source_ids", []):
        out.setdefault(pid, "summary")
    return out


def profile_text(db: Session, user: User) -> str:
    """The corrected profile as plain text: evidence for tailoring and answers."""
    from app.services.profile import full_profile

    fp = full_profile(db, user)
    lines = [f"Summary: {fp.profile.summary or ''}"]
    lines += [f"Experience: {e.title or ''} at {e.employer}. " + " ".join(e.bullets) for e in fp.experiences]
    lines += [f"Project: {p.name}. {p.description or ''} {' '.join(p.technologies)}" for p in fp.projects]
    lines += [f"Education: {e.degree or ''} {e.field or ''}, {e.institution} {e.grade or ''}" for e in fp.education]
    lines += [f"Certification: {c.name} {c.issuer or ''}" for c in fp.certifications]
    lines.append("Skills: " + ", ".join(s.name for s in fp.skills))
    return "\n".join(lines)
