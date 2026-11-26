"""Deterministic resume understanding.

Pipeline: document → lines (with source paragraph ids and formatting hints) → semantic section
detection (synonym lexicon + fuzzy matching + layout cues) → per-section entity extraction →
experience categorisation → skill normalisation with evidence → quality assessment.

Nothing is invented: every extracted value carries the paragraph id(s) it came from, and
fields that can't be found are left empty and reported as missing.
"""

from __future__ import annotations

import difflib
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from pypdf import PdfReader

from app.domain import taxonomy
from app.domain.docx_document import read_paragraphs

# ── Section lexicon ────────────────────────────────────────────────────────────────────────────
SECTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "summary": (
        "summary",
        "professional summary",
        "profile",
        "professional profile",
        "about me",
        "about",
        "career objective",
        "objective",
        "career summary",
        "executive summary",
        "overview",
        "introduction",
    ),
    "experience": (
        "experience",
        "professional experience",
        "work experience",
        "work history",
        "employment",
        "employment history",
        "career history",
        "relevant experience",
        "industry experience",
    ),
    "internships": ("internships", "internship", "internship experience", "internships and training", "training"),
    "freelance": ("freelance", "freelance experience", "freelance work", "consulting", "contract work"),
    "projects": (
        "projects",
        "technical projects",
        "personal projects",
        "key projects",
        "side projects",
        "selected projects",
        "project experience",
        "project work",
    ),
    "academic_projects": (
        "academic projects",
        "university projects",
        "college projects",
        "course projects",
        "capstone project",
        "final year project",
        "research projects",
    ),
    "education": (
        "education",
        "academic background",
        "academics",
        "educational qualifications",
        "qualifications",
        "academic qualifications",
        "education and training",
    ),
    "skills": (
        "skills",
        "technical skills",
        "core skills",
        "key skills",
        "core competencies",
        "competencies",
        "technologies",
        "tech stack",
        "tools and technologies",
        "skills and tools",
        "areas of expertise",
        "expertise",
        "technical proficiency",
        "skill set",
        "skillset",
    ),
    "certifications": (
        "certifications",
        "certificates",
        "licenses and certifications",
        "courses",
        "certifications and courses",
        "professional development",
        "licenses",
    ),
    "achievements": (
        "achievements",
        "awards",
        "honors",
        "honours",
        "accomplishments",
        "awards and achievements",
        "honors and awards",
        "scholarships",
    ),
    "publications": ("publications", "papers", "research"),
    "leadership": (
        "leadership",
        "positions of responsibility",
        "extracurricular activities",
        "extracurriculars",
        "volunteering",
        "volunteer experience",
        "activities",
        "community",
    ),
    "languages": ("languages", "spoken languages"),
    "interests": ("interests", "hobbies", "hobbies and interests"),
    "contact": ("contact", "contact information", "personal details", "personal information"),
}
_SYN_INDEX = {syn: canon for canon, syns in SECTION_SYNONYMS.items() for syn in syns}

_MONTHS = {
    m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])
}
_MONTHS["sept"] = 9
_DATE_TOKEN = (  # noqa: S105 - regex, not a credential
    r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*,?\s*\d{4}"
    r"|\d{1,2}/\d{4}|\d{4})"
)
_DATE_RANGE = re.compile(
    rf"({_DATE_TOKEN})\s*(?:-|–|—|to|until)\s*({_DATE_TOKEN}|present|current|now|ongoing|till date|today)", re.I
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,5}")
_LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w-]+/?", re.I)
_GITHUB = re.compile(r"(?:https?://)?github\.com/[\w-]+/?", re.I)
_URL = re.compile(r"https?://[^\s|,]+", re.I)
_BULLET = re.compile(r"^\s*[•▪●◦\-*–·➢►✓]\s*")
_DEGREE = re.compile(
    r"\b(b\.?\s?tech|b\.?\s?e\b|b\.?\s?sc|bsc|b\.?\s?com|bca|bba|b\.?\s?a\b|bachelor|m\.?\s?tech|m\.?\s?e\b|m\.?\s?sc|msc|"
    r"mca|mba|m\.?\s?s\b|master|ph\.?\s?d|doctor|diploma|associate|high school|secondary|senior secondary|"
    r"intermediate|hsc|ssc|class xii|class x|12th|10th|cbse|icse)",
    re.I,
)
_GRADE = re.compile(
    r"\b(?:c?gpa|cpi|grade|percentage|score)\s*[:\-]?\s*(\d{1,2}(?:\.\d{1,2})?\s*(?:/\s*\d{1,2}(?:\.\d)?)?%?)"
    r"|(\d{2}(?:\.\d{1,2})?\s*%)",
    re.I,
)
_INSTITUTION = re.compile(r"\b(university|college|institute|school|academy|iit|nit|iiit|bits|polytechnic)\b", re.I)
_NUMBER = re.compile(r"\d")
_WEAK_STARTS = (
    "responsible for",
    "worked on",
    "helped",
    "assisted",
    "involved in",
    "participated in",
    "duties included",
    "tasked with",
    "handled",
)
_ACTION_VERBS = re.compile(
    r"^(led|built|designed|developed|implemented|created|launched|improved|reduced|increased|"
    r"optimized|optimised|automated|architected|delivered|migrated|shipped|scaled|owned|drove|"
    r"analyzed|analysed|engineered|established|streamlined|mentored|managed|spearheaded|wrote|"
    r"integrated|deployed|refactored|resolved|negotiated|grew|generated|achieved|won|"
    r"published|presented|organized|organised|researched|trained|configured|maintained)\b",
    re.I,
)


@dataclass
class Line:
    id: str
    text: str
    is_bold: bool = False
    is_list: bool = False
    style: str = ""
    font_size: float | None = None


@dataclass
class Section:
    canonical_type: str
    raw_heading: str | None
    confidence: float
    lines: list[Line] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass
class ExperienceEntry:
    category: str  # professional | internship | freelance
    employer: str
    title: str | None
    location: str | None
    start_date: str | None
    end_date: str | None
    is_current: bool
    bullets: list[str]
    source_ids: list[str]


@dataclass
class ProjectEntry:
    category: str  # academic | personal | professional
    name: str
    description: str
    technologies: list[str]
    url: str | None
    source_ids: list[str]


@dataclass
class EducationEntry:
    institution: str
    degree: str | None
    field: str | None
    start_date: str | None
    end_date: str | None
    grade: str | None
    source_ids: list[str]


@dataclass
class ParsedResume:
    contact: dict[str, str | None]
    summary: str | None
    sections: list[dict]
    experiences: list[ExperienceEntry]
    projects: list[ProjectEntry]
    education: list[EducationEntry]
    skills: dict[str, list[str]]  # canonical → evidence
    unrecognised_skills: list[str]
    certifications: list[dict]
    achievements: list[str]
    years_experience: float
    internship_months: int
    seniority: str
    missing: list[str]
    quality: dict
    text: str

    def as_dict(self) -> dict:
        return asdict(self)


# ── Document → lines ───────────────────────────────────────────────────────────────────────────
def lines_from_docx(data: bytes) -> list[Line]:
    out: list[Line] = []
    for p in read_paragraphs(data):
        if p.location == "footer":
            continue
        # A paragraph can hold soft line breaks; keep one Line per paragraph to preserve the id map.
        out.append(Line(p.id, p.text.replace("\t", "  ").strip(), p.is_bold, p.is_list, p.style, p.font_size))
    return out


def lines_from_pdf(data: bytes) -> list[Line]:
    reader = PdfReader(io.BytesIO(data))
    out: list[Line] = []
    n = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            t = raw.strip()
            if t:
                out.append(Line(f"l{n}", t, is_list=bool(_BULLET.match(t))))
                n += 1
    return out


# ── Sections ───────────────────────────────────────────────────────────────────────────────────
def _heading_key(text: str) -> str:
    t = re.sub(r"[^a-z& ]", " ", text.lower()).replace("&", "and")
    return re.sub(r"\s+", " ", t).strip()


def classify_heading(line: Line) -> tuple[str, float] | None:
    """Return (canonical section, confidence) if this line is a section heading."""
    text = line.text.strip().rstrip(":")
    if not text or len(text) > 48 or _BULLET.match(line.text) or _DATE_RANGE.search(text):
        return None
    words = text.split()
    if len(words) > 5:
        return None
    key = _heading_key(text)
    looks_like_heading = (
        line.is_bold
        or text.isupper()
        or line.style.lower().startswith(("heading", "title"))
        or line.text.strip().endswith(":")
        or (line.font_size or 0) >= 12.5
    )
    if key in _SYN_INDEX:
        return _SYN_INDEX[key], 0.98 if looks_like_heading else 0.85
    close = difflib.get_close_matches(key, _SYN_INDEX.keys(), n=1, cutoff=0.82)
    if close and looks_like_heading:
        return _SYN_INDEX[close[0]], 0.75
    # Headings like "Technical Skills & Tools" or "Professional Experience (5 yrs)": keyword containment.
    if looks_like_heading:
        for syn, canon in sorted(_SYN_INDEX.items(), key=lambda kv: -len(kv[0])):
            if len(syn) > 4 and re.search(rf"\b{re.escape(syn)}\b", key):
                return canon, 0.7
        # Conversational headings ("Where I've worked", "What I've built"): cue words.
        for cue, canon in _HEADING_CUES:
            if re.search(cue, key):
                return canon, 0.6
    return None


_HEADING_CUES: tuple[tuple[str, str], ...] = (
    (r"\bintern", "internships"),
    (r"\b(worked|work|jobs|career|employ)", "experience"),
    (r"\b(built|build|made|portfolio)\b", "projects"),
    (r"\b(studied|study|school|degree)", "education"),
    (r"\b(know|tools|stack|tech)\b", "skills"),
    (r"\b(about|who i am)\b", "summary"),
)


def split_sections(lines: list[Line]) -> tuple[list[Line], list[Section]]:
    header: list[Line] = []
    sections: list[Section] = []
    current: Section | None = None
    for line in lines:
        hit = classify_heading(line)
        if hit:
            current = Section(hit[0], line.text.strip().rstrip(":"), hit[1])
            sections.append(current)
        elif current is None:
            header.append(line)
        else:
            current.lines.append(line)
    return header, sections


# ── Dates ──────────────────────────────────────────────────────────────────────────────────────
def parse_date(token: str, *, end: bool = False) -> date | None:
    t = token.strip().lower().rstrip(".")
    m = re.match(r"([a-z]+)\.?\s*,?\s*(\d{4})", t)
    if m and m.group(1)[:4].rstrip("t") in _MONTHS | {"sept": 9}:
        mon = _MONTHS.get(m.group(1)[:3], 1)
        return date(int(m.group(2)), mon, 1)
    m = re.match(r"(\d{1,2})/(\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m = re.match(r"(\d{4})$", t)
    if m:
        return date(int(m.group(1)), 12 if end else 1, 1)
    return None


def _date_range(text: str) -> tuple[date | None, date | None, bool, str | None]:
    m = _DATE_RANGE.search(text)
    if not m:
        return None, None, False, None
    start = parse_date(m.group(1))
    end_raw = m.group(2).lower()
    current = end_raw in ("present", "current", "now", "ongoing", "till date", "today")
    end = None if current else parse_date(end_raw, end=True)
    return start, end, current, m.group(0)


def _months_between(a: date, b: date) -> int:
    return max(0, (b.year - a.year) * 12 + (b.month - a.month) + 1)


def total_months(ranges: list[tuple[date, date]]) -> int:
    """Union of date ranges in months, so overlapping roles aren't double counted."""
    if not ranges:
        return 0
    rs = sorted(ranges)
    merged = [list(rs[0])]
    for s, e in rs[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return sum(_months_between(s, e) for s, e in merged)


# ── Entries ────────────────────────────────────────────────────────────────────────────────────
def _strip_bullet(text: str) -> str:
    return _BULLET.sub("", text).strip()


def _is_entry_header(line: Line) -> bool:
    if line.is_list or _BULLET.match(line.text):
        return False
    if _DATE_RANGE.search(line.text):
        return True
    words = len(line.text.split())
    return line.is_bold and words <= 14


def _group_entries(lines: list[Line]) -> list[list[Line]]:
    """Group a section's lines into entries: header line(s) followed by bullets/description."""
    groups: list[list[Line]] = []
    current: list[Line] = []
    seen_body = False
    for line in lines:
        if _is_entry_header(line) and (seen_body or not current):
            if current:
                groups.append(current)
            current, seen_body = [line], False
        else:
            if not _is_entry_header(line):
                seen_body = True
            current.append(line)
    if current:
        groups.append(current)
    return groups


_TITLE_HINT = re.compile(
    r"\b(engineer|developer|intern|analyst|manager|designer|scientist|consultant|associate|lead|"
    r"architect|specialist|administrator|coordinator|executive|officer|assistant|trainee|founder|"
    r"director|researcher|programmer|tester|qa|sde|swe|head|representative|accountant|freelancer)\b",
    re.I,
)


def _split_role(header_text: str) -> tuple[str, str | None, str | None]:
    """Split "Software Engineer at Acme, Bengaluru" style headers into (employer, title, location)."""
    text = _DATE_RANGE.sub("", header_text)
    text = re.sub(r"\s{2,}", " | ", text.strip(" |,–-"))
    parts = [p.strip(" ,–-|") for p in re.split(r"\s+(?:at|@)\s+|\s*[|•·]\s*|\s+[–—-]\s+", text) if p.strip(" ,–-|")]
    if not parts:
        return header_text.strip(), None, None
    title = next((p for p in parts if _TITLE_HINT.search(p)), None)
    rest = [p for p in parts if p != title]
    employer = rest[0] if rest else (title or parts[0])
    location = rest[1] if len(rest) > 1 else None
    if "," in employer and not location:
        employer, _, location = employer.partition(",")
        employer, location = employer.strip(), location.strip() or None
    return employer, title, location


def parse_experience(section: Section, default_category: str) -> list[ExperienceEntry]:
    out: list[ExperienceEntry] = []
    for group in _group_entries(section.lines):
        headers = [ln for ln in group if not (ln.is_list or _BULLET.match(ln.text))][:2]
        if not headers:
            continue
        header_text = "  |  ".join(h.text for h in headers)
        start, end, current, _ = _date_range(header_text)
        employer, title, location = _split_role(headers[0].text)
        if len(headers) > 1 and not _DATE_RANGE.fullmatch(headers[1].text.strip()):
            # Two-line headers: "Acme Corp" / "Software Engineer   2021 – 2023" (either order).
            e2, t2, l2 = _split_role(headers[1].text)
            if title is None and t2:
                title = t2
            elif title and employer == title and e2 != title:
                employer = e2
            location = location or l2
        bullets = [_strip_bullet(ln.text) for ln in group if ln not in headers]
        blob = f"{header_text} {title or ''}".lower()
        if default_category == "internship" or re.search(r"\bintern(ship)?\b", blob):
            category = "internship"
        elif default_category == "freelance" or re.search(r"\b(freelance|freelancer|self-employed|contract)\b", blob):
            category = "freelance"
        else:
            category = "professional"
        out.append(
            ExperienceEntry(
                category=category,
                employer=employer[:200],
                title=(title or None) and title[:200],
                location=location,
                start_date=start.isoformat() if start else None,
                end_date=end.isoformat() if end else None,
                is_current=current,
                bullets=[b for b in bullets if b],
                source_ids=[ln.id for ln in group],
            )
        )
    return out


def parse_projects(section: Section, default_category: str) -> list[ProjectEntry]:
    out: list[ProjectEntry] = []
    for group in _group_entries(section.lines):
        head = group[0]
        name = _DATE_RANGE.sub("", _strip_bullet(head.text)).strip(" |,–-:")
        name = re.split(r"\s+[|–—]\s+|:\s", name)[0][:200] or "Untitled project"
        body = "\n".join(_strip_bullet(ln.text) for ln in group[1:]) or _strip_bullet(head.text)
        blob = " ".join(ln.text for ln in group)
        url = _URL.search(blob) or _GITHUB.search(blob)
        cat = default_category
        if re.search(
            r"\b(course|coursework|university|college|semester|capstone|thesis|final[- ]year|academic)\b", blob, re.I
        ):
            cat = "academic"
        out.append(
            ProjectEntry(
                cat,
                name,
                body,
                list(taxonomy.extract_skills(blob)),
                url.group(0) if url else None,
                [ln.id for ln in group],
            )
        )
    return out


def parse_education(section: Section) -> list[EducationEntry]:
    out: list[EducationEntry] = []
    groups: list[list[Line]] = []
    for line in section.lines:
        if (
            _INSTITUTION.search(line.text)
            and (not groups or any(_INSTITUTION.search(x.text) for x in groups[-1]))
            or not groups
        ):
            groups.append([line])
        else:
            groups[-1].append(line)
    for group in groups:
        blob = " | ".join(ln.text for ln in group)
        inst_line = next((ln.text for ln in group if _INSTITUTION.search(ln.text)), group[0].text)
        institution = re.split(r"\s*[|,–—]\s*(?=\D)", _DATE_RANGE.sub("", inst_line))[0].strip(" |,–-") or inst_line
        degree_line = next((ln.text for ln in group if _DEGREE.search(ln.text)), None)
        degree, fld = None, None
        if degree_line:
            dl = _DATE_RANGE.sub("", degree_line)
            dl = _GRADE.sub("", dl).strip(" |,–-")
            m = re.split(r"\s+in\s+|,\s*|\s+[–—-]\s+|\s*\|\s*", dl, maxsplit=1)
            degree = m[0].strip()[:200]
            fld = m[1].strip()[:200] if len(m) > 1 and not _INSTITUTION.search(m[1]) else None
        start, end, _, _ = _date_range(blob)
        if not start:
            # A lone graduation year ("Class of 2024") is recorded as the end date only.
            yrs = re.findall(r"\b(?:19|20)\d{2}\b", blob)
            if yrs:
                end = date(int(yrs[-1]), 6, 1)
        g = _GRADE.search(blob)
        grade = (g.group(1) or g.group(2)).strip() if g else None
        out.append(
            EducationEntry(
                institution[:300],
                degree,
                fld,
                start.isoformat() if start else None,
                end.isoformat() if end else None,
                grade,
                [ln.id for ln in group],
            )
        )
    return out


def parse_skills_section(section: Section) -> tuple[dict[str, list[str]], list[str]]:
    known: dict[str, list[str]] = {}
    unknown: list[str] = []
    for line in section.lines:
        text = _strip_bullet(line.text)
        # "Languages: Python, Go" → drop the label.
        if ":" in text and len(text.split(":", 1)[0]) < 40:
            text = text.split(":", 1)[1]
        for token in re.split(r"[,|;•/]|\s{2,}|\band\b", text):
            tok = token.strip(" .()")
            if not tok or len(tok) > 40:
                continue
            canon = taxonomy.canonicalize(tok)
            if canon:
                known.setdefault(canon, []).append(line.text.strip())
            elif len(tok.split()) <= 3 and not tok.isdigit():
                unknown.append(tok)
        for canon, ev in taxonomy.extract_skills(line.text).items():
            known.setdefault(canon, ev)
    seen: set[str] = set()
    return known, [u for u in unknown if not (u.lower() in seen or seen.add(u.lower()))]  # type: ignore[func-returns-value]


def parse_contact(header: list[Line], all_text: str) -> dict[str, str | None]:
    head_text = "\n".join(ln.text for ln in header) or all_text[:600]
    email = _EMAIL.search(head_text) or _EMAIL.search(all_text)
    phone = None
    for m in _PHONE.finditer(head_text):
        digits = re.sub(r"\D", "", m.group(0))
        if 10 <= len(digits) <= 13 and not re.fullmatch(r"(19|20)\d{2}(19|20)\d{2}", digits):
            phone = m.group(0).strip()
            break
    linkedin = _LINKEDIN.search(all_text)
    github = _GITHUB.search(all_text)
    name = None
    for ln in header[:4]:
        t = ln.text.strip()
        if _EMAIL.search(t) or _PHONE.search(t) or "http" in t or len(t.split()) > 5 or any(ch.isdigit() for ch in t):
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,60}", t):
            name = t.title() if t.isupper() else t
            break
    location = None
    for ln in header[:6]:
        m = re.search(r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?,\s*(?:[A-Z][a-zA-Z]+|[A-Z]{2}))\b", ln.text)
        if m and not _EMAIL.search(m.group(1)):
            location = m.group(1)
            break
    return {
        "name": name,
        "email": email.group(0) if email else None,
        "phone": phone,
        "linkedin": linkedin.group(0) if linkedin else None,
        "github": github.group(0) if github else None,
        "location": location,
    }


# ── Quality ────────────────────────────────────────────────────────────────────────────────────
def assess_quality(
    parsed_sections: list[Section],
    contact: dict,
    experiences: list[ExperienceEntry],
    projects: list[ProjectEntry],
    skills: dict,
    word_count: int,
) -> dict:
    issues: list[dict] = []

    def issue(severity: str, code: str, message: str, ref: str | None = None) -> None:
        issues.append({"severity": severity, "code": code, "message": message, "paragraph_id": ref})

    present = {s.canonical_type for s in parsed_sections}
    if not contact.get("email"):
        issue("high", "missing_email", "No email address found.")
    if not contact.get("phone"):
        issue("medium", "missing_phone", "No phone number found.")
    if "summary" not in present:
        issue("low", "missing_summary", "Add a 2–3 line summary tailored to your target role.")
    if not present & {"experience", "internships", "freelance"} and not projects:
        issue("high", "no_experience", "No experience or projects section was detected.")
    if "education" not in present:
        issue("medium", "missing_education", "No education section was detected.")
    if len(skills) < 5:
        issue("medium", "few_skills", "Fewer than five recognisable skills — list your tools and technologies.")

    bullets = [(b, e) for e in experiences for b in e.bullets]
    quantified = sum(1 for b, _ in bullets if _NUMBER.search(b))
    for e in experiences:
        if not e.start_date:
            issue("medium", "missing_dates", f"Dates are missing for {e.employer}.", e.source_ids[0])
        if not e.bullets:
            issue("medium", "no_bullets", f"Describe what you achieved at {e.employer}.", e.source_ids[0])
        for b, sid in zip(e.bullets, e.source_ids[1:], strict=False):
            low = b.lower()
            if low.startswith(_WEAK_STARTS):
                issue("low", "weak_verb", f"Starts with a weak phrase: “{b[:60]}…”. Lead with an action verb.", sid)
            elif len(b.split()) > 38:
                issue("low", "long_bullet", f"Long bullet ({len(b.split())} words) — split or tighten it.", sid)
    starts = [b.split()[0].lower() for b, _ in bullets if b.split()]
    for verb in {s for s in starts if starts.count(s) >= 3}:
        issue("low", "repetition", f"“{verb.capitalize()}” starts {starts.count(verb)} bullets — vary your verbs.")
    if bullets and quantified / len(bullets) < 0.3:
        issue("medium", "few_metrics", f"Only {quantified} of {len(bullets)} bullets include a measurable result.")
    if word_count < 200:
        issue("medium", "too_short", "The resume is very short; it may be missing detail.")
    elif word_count > 1100:
        issue("low", "too_long", "The resume is long; consider trimming to the most relevant content.")

    penalty = {"high": 15, "medium": 7, "low": 3}
    score = max(0, 100 - sum(penalty[i["severity"]] for i in issues))
    action = sum(1 for b, _ in bullets if _ACTION_VERBS.match(b))
    return {
        "score": score,
        "issues": issues,
        "metrics": {
            "bullets": len(bullets),
            "quantified_bullets": quantified,
            "action_verb_bullets": action,
            "word_count": word_count,
            "skills": len(skills),
            "sections": sorted(present),
        },
    }


# ── Orchestration ──────────────────────────────────────────────────────────────────────────────
def parse_lines(lines: list[Line], today: date | None = None) -> ParsedResume:
    today = today or date.today()
    header, sections = split_sections(lines)
    all_text = "\n".join(ln.text for ln in lines)
    contact = parse_contact(header, all_text)

    experiences: list[ExperienceEntry] = []
    projects: list[ProjectEntry] = []
    education: list[EducationEntry] = []
    skills: dict[str, list[str]] = {}
    unrecognised: list[str] = []
    certifications: list[dict] = []
    achievements: list[str] = []
    summary_parts: list[str] = []

    for s in sections:
        t = s.canonical_type
        if t == "summary":
            summary_parts.append(s.text)
        elif t == "experience":
            experiences += parse_experience(s, "professional")
        elif t == "internships":
            experiences += parse_experience(s, "internship")
        elif t == "freelance":
            experiences += parse_experience(s, "freelance")
        elif t == "projects":
            projects += parse_projects(s, "personal")
        elif t == "academic_projects":
            projects += parse_projects(s, "academic")
        elif t == "education":
            education += parse_education(s)
        elif t == "skills":
            k, u = parse_skills_section(s)
            for name, ev in k.items():
                skills.setdefault(name, ev)
            unrecognised += u
        elif t == "certifications":
            for ln in s.lines:
                name = _strip_bullet(ln.text)
                issuer = None
                m = re.split(r"\s+[–—|-]\s+|\s+by\s+|,\s*", name, maxsplit=1)
                if len(m) > 1:
                    name, issuer = m[0], re.sub(_DATE_TOKEN, "", m[1], flags=re.I).strip(" ,()-") or None
                certifications.append({"name": name[:300], "issuer": issuer, "source_id": ln.id})
        elif t == "achievements":
            achievements += [_strip_bullet(ln.text) for ln in s.lines]

    # Skills mentioned anywhere (experience bullets, projects) count, with their evidence.
    for name, ev in taxonomy.extract_skills(all_text).items():
        skills.setdefault(name, ev)

    def _ranges(cats: set[str]) -> list[tuple[date, date]]:
        out = []
        for e in experiences:
            if e.category in cats and e.start_date:
                s = date.fromisoformat(e.start_date)
                end = date.fromisoformat(e.end_date) if e.end_date else today if e.is_current else None
                if end and end >= s:
                    out.append((s, min(end, today)))
        return out

    pro_months = total_months(_ranges({"professional", "freelance"}))
    intern_months = total_months(_ranges({"internship"}))
    years = round(pro_months / 12, 1)
    seniority = "fresher" if years < 0.5 else "junior" if years < 2 else "mid" if years < 5 else "senior"

    missing = [k for k, v in contact.items() if v is None and k in ("name", "email", "phone")]
    present_types = {s.canonical_type for s in sections}
    for want in ("summary", "education", "skills"):
        if want not in present_types:
            missing.append(f"section:{want}")
    if not experiences:
        missing.append("experience")

    quality = assess_quality(sections, contact, experiences, projects, skills, len(all_text.split()))
    return ParsedResume(
        contact=contact,
        summary="\n".join(summary_parts) or None,
        sections=[
            {
                "canonical_type": s.canonical_type,
                "raw_heading": s.raw_heading,
                "confidence": s.confidence,
                "text": s.text,
                "paragraph_ids": [ln.id for ln in s.lines],
            }
            for s in sections
        ],
        experiences=experiences,
        projects=projects,
        education=education,
        skills=skills,
        unrecognised_skills=unrecognised[:40],
        certifications=certifications,
        achievements=achievements,
        years_experience=years,
        internship_months=intern_months,
        seniority=seniority,
        missing=missing,
        quality=quality,
        text=all_text,
    )


def parse_document(data: bytes, file_format: str, today: date | None = None) -> ParsedResume:
    lines = lines_from_docx(data) if file_format == "docx" else lines_from_pdf(data)
    if not lines:
        raise ValueError("No readable text found. Scanned (image-only) PDFs are not supported yet.")
    return parse_lines(lines, today)
