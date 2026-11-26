"""Extract structured facts from untrusted job-description text.

Everything here is deterministic. When a fact can't be found it stays `None` — we never guess.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field

import nh3

from app.domain import taxonomy

_SPACE = re.compile(r"\s+")


def clean_html(raw: str | None) -> tuple[str, str]:
    """Returns (sanitised_html, plain_text). Provider HTML is sometimes entity-escaped twice."""
    if not raw:
        return "", ""
    unescaped = html.unescape(raw)
    if "&lt;" in unescaped:
        unescaped = html.unescape(unescaped)
    safe = nh3.clean(
        unescaped,
        tags={"p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "h1", "h2", "h3", "h4", "a", "div", "span"},
        attributes={"a": {"href"}},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
    )
    text = re.sub(r"<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", unescaped, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return safe, text


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.sub(r"[^a-z0-9+#/ ]", " ", t)
    t = t.replace("sr ", "senior ").replace("jr ", "junior ").replace("engg", "engineer")
    return _SPACE.sub(" ", t).strip()


def normalize_company(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\b(inc|llc|ltd|limited|pvt|private|corp|corporation|co|gmbh|technologies)\b\.?", " ", n)
    return _SPACE.sub(" ", re.sub(r"[^a-z0-9 ]", " ", n)).strip()


def fingerprint(company: str, title: str, location: str | None) -> str:
    """Cross-source duplicate key: same company + normalised title + primary location."""
    loc = normalize_title((location or "").split(",")[0])
    key = f"{normalize_company(company)}|{normalize_title(title)}|{loc}"
    return hashlib.sha256(key.encode()).hexdigest()


_EXP_RANGE = re.compile(r"(\d{1,2})(?:\.\d)?\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years|yrs|year)", re.I)
_EXP_MIN = re.compile(
    r"(?:at least|minimum(?: of)?|min\.?)?\s*(\d{1,2})\s*\+?\s*(?:\+|plus)?\s*(?:years|yrs|year)(?:'s)?\s*(?:of\s+)?"
    r"(?:professional\s+|relevant\s+|industry\s+|hands-on\s+|work\s+)*(?:experience|exp)",
    re.I,
)


def experience_range(text: str) -> tuple[float | None, float | None]:
    m = _EXP_RANGE.search(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo <= hi <= 30:
            return lo, hi
    mins = [float(x) for x in _EXP_MIN.findall(text) if float(x) <= 30]
    if mins:
        return min(mins), None
    if re.search(
        r"\b(fresher|freshers|new grad|new graduate|entry[- ]level|recent graduate|graduate program)\b", text, re.I
    ):
        return 0.0, 1.0
    return None, None


def seniority(title: str, exp_min: float | None) -> str | None:
    t = title.lower()
    if re.search(r"\bintern(ship)?\b", t):
        return "intern"
    if re.search(r"\b(principal|staff|distinguished|director|head of|vp)\b", t):
        return "staff+"
    if re.search(r"\b(senior|sr\.?|lead)\b", t):
        return "senior"
    if re.search(r"\b(junior|jr\.?|associate|graduate|entry|new grad|trainee|apprentice)\b", t):
        return "junior"
    if exp_min is not None:
        return "junior" if exp_min < 2 else "mid" if exp_min < 5 else "senior"
    return None


def work_mode(*hints: str | None) -> str | None:
    blob = " ".join(h for h in hints if h).lower()
    if not blob:
        return None
    if "hybrid" in blob:
        return "hybrid"
    if re.search(r"\bremote\b", blob):
        return "remote"
    if re.search(r"\b(on-?site|in[- ]office|in office)\b", blob):
        return "onsite"
    return None


def employment_type(title: str, hint: str | None) -> tuple[str | None, bool]:
    h = (hint or "").lower().replace("-", "").replace(" ", "")
    t = title.lower()
    if "intern" in h or re.search(r"\bintern(ship)?\b", t):
        return "internship", True
    if "contract" in h or "temporary" in h or re.search(r"\bcontract(or)?\b", t):
        return "contract", False
    if "parttime" in h:
        return "part_time", False
    if "fulltime" in h or "permanent" in h or "regular" in h:
        return "full_time", False
    return None, False


@dataclass
class SalaryParse:
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    period: str | None = None
    text: str | None = None


_CUR = {
    "$": "USD",
    "usd": "USD",
    "₹": "INR",
    "inr": "INR",
    "rs": "INR",
    "rs.": "INR",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "cad": "CAD",
    "sgd": "SGD",
    "aud": "AUD",
}
_LPA = re.compile(
    r"(?:₹|inr|rs\.?)?\s*(\d{1,3}(?:\.\d+)?)\s*(?:-|–|to)\s*(\d{1,3}(?:\.\d+)?)\s*(?:lpa|lakhs?(?: per annum)?)", re.I
)
_LPA_SINGLE = re.compile(r"(?:₹|inr|rs\.?)?\s*(\d{1,3}(?:\.\d+)?)\s*(?:lpa|lakhs? per annum)", re.I)
_MONEY_RANGE = re.compile(
    r"(\$|₹|€|£|usd|inr|eur|gbp|cad|sgd|aud)\s?(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:-|–|to)\s*(?:\$|₹|€|£|usd|inr|eur|gbp|cad|sgd|aud)?\s?(\d[\d,]*(?:\.\d+)?)\s*(k)?"
    r"(?:\s*(?:per|/|a)\s*(year|yr|annum|month|mo|hour|hr))?",
    re.I,
)


def parse_salary_text(text: str) -> SalaryParse:
    m = _LPA.search(text)
    if m:
        return SalaryParse(float(m.group(1)) * 100_000, float(m.group(2)) * 100_000, "INR", "year", m.group(0).strip())
    m = _LPA_SINGLE.search(text)
    if m:
        v = float(m.group(1)) * 100_000
        return SalaryParse(v, v, "INR", "year", m.group(0).strip())
    m = _MONEY_RANGE.search(text)
    if m:
        cur = _CUR.get(m.group(1).lower())
        lo = float(m.group(2).replace(",", "")) * (1000 if m.group(3) else 1)
        hi = float(m.group(4).replace(",", "")) * (1000 if m.group(5) or m.group(3) else 1)
        per = (m.group(6) or "").lower()
        period = "month" if per.startswith("mo") else "hour" if per.startswith("h") else "year"
        if hi < lo or lo < 10:
            return SalaryParse()
        return SalaryParse(lo, hi, cur, period, m.group(0).strip())
    return SalaryParse()


_PREFERRED_HDR = re.compile(r"(nice to have|preferred|bonus|plus if|good to have|desirable|pluses)", re.I)
_REQUIRED_HDR = re.compile(
    r"(requirements|qualifications|what you.?ll need|must have|you have|what we.?re looking for|"
    r"who you are|minimum qualifications|skills)",
    re.I,
)


@dataclass
class SkillSplit:
    required: list[str] = field(default_factory=list)
    preferred: list[str] = field(default_factory=list)


def split_skills(text: str) -> SkillSplit:
    """Classify skills as required vs preferred using the section in which they appear."""
    required: dict[str, None] = {}
    preferred: dict[str, None] = {}
    mode = "required"
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 80 and _PREFERRED_HDR.search(stripped):
            mode = "preferred"
        elif len(stripped) < 80 and _REQUIRED_HDR.search(stripped):
            mode = "required"
        line_mode = "preferred" if _PREFERRED_HDR.search(stripped) else mode
        for skill in taxonomy.extract_skills(stripped):
            (preferred if line_mode == "preferred" else required)[skill] = None
    for s in list(preferred):
        if s in required:
            del preferred[s]
    return SkillSplit(list(required), list(preferred))
