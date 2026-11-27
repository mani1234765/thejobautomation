"""Extract structured facts from untrusted job-description text.

Everything here is deterministic. When a fact can't be found it stays `None` — we never guess.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field

import nh3

from app.domain import fx, taxonomy

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
    currency: str | None = None  # ISO code, "$" when only an ambiguous dollar sign is shown, None when missing
    period: str | None = None  # year | month | hour | None (not stated — never assumed)
    text: str | None = None  # the matched wording, for evidence


# ── Salary text ────────────────────────────────────────────────────────────────────────────────
# One pattern finds an amount or a range with optional currency markers and units; acceptance rules then
# decide whether it is really pay (see _accept). Periods are taken only from explicit wording.
_CUR = r"(?:us\$|ca\$|c\$|au\$|a\$|s\$|\$|₹|€|£|zł|\brs\.?(?=\s?\d)|\b(?:inr|usd|eur|gbp|cad|sgd|aud|pln)\b)"
_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_UNIT = r"(?:k\b|lpa\b|lakhs?\b|lacs?\b|l\b|crores?\b|cr\b|million\b|mn\b|m\b|billion\b|bn\b)"
_AMOUNT = re.compile(
    rf"(?<![\w.,])(?P<c1>{_CUR})?\s?(?P<n1>{_NUM})\s*(?P<u1>{_UNIT})?"
    rf"(?:\s*(?P<sep>-|–|—|to|and)\s*(?P<c2>{_CUR})?\s?(?P<n2>{_NUM})\s*(?P<u2>{_UNIT})?)?"
    rf"(?:\s?(?P<c3>{_CUR}))?",
    re.I,
)
_MULT = {
    "k": 1e3,
    "l": 1e5,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "lpa": 1e5,
    "cr": 1e7,
    "crore": 1e7,
    "crores": 1e7,
}
_BIG = {"m", "mn", "million", "billion", "bn"}  # company figures ("$50M Series B"), never salaries here
_PERIOD_AFTER = re.compile(
    r"^\s*(?:(?:/|per|an?)\s*(?P<w>year|yr|annum|month|mo|hour|hr)\b|(?P<adv>annually|yearly|monthly|hourly)\b|"
    r"(?P<pa>p\.?\s?a\b\.?)|(?P<pm>p\.?\s?m\.?(?!\w))|\(?(?P<lpa>lpa)\b)",
    re.I,
)
_PERIOD_BEFORE = re.compile(
    r"\b(?P<w>monthly|yearly|annual|annually|hourly|per month|per annum|per year)\b[^.\n]{0,25}$", re.I
)
_PAY_WORDS = re.compile(
    r"\b(salary|salaries|stipend|ctc|compensation|pay|wage|wages|remuneration|package|rate|base)\b", re.I
)
_WORD_PERIOD = {
    "year": "year",
    "yr": "year",
    "annum": "year",
    "annually": "year",
    "yearly": "year",
    "annual": "year",
    "per annum": "year",
    "per year": "year",
    "month": "month",
    "mo": "month",
    "monthly": "month",
    "per month": "month",
    "hour": "hour",
    "hr": "hour",
    "hourly": "hour",
}
# Plausible pay, by period, in the listed currency (the bound is generous; it only rejects company figures).
_MAX_PLAUSIBLE = {"year": 5e7, "month": 5e6, "hour": 1e5, None: 5e7}


def _num(v: str) -> float:
    return float(v.replace(",", ""))


def _period(text: str, start: int, end: int, unit: str | None) -> str | None:
    if unit and unit.lower() == "lpa":
        return "year"
    m = _PERIOD_AFTER.match(text[end : end + 30])
    if m:
        if m.group("w"):
            return _WORD_PERIOD[m.group("w").lower()]
        if m.group("adv"):
            return _WORD_PERIOD[m.group("adv").lower()]
        if m.group("pa") or m.group("lpa"):
            return "year"
        if m.group("pm"):
            return "month"
    b = _PERIOD_BEFORE.search(text[max(0, start - 40) : start])
    return _WORD_PERIOD.get(b.group("w").lower()) if b else None


def _candidate(text: str, m: re.Match[str]) -> SalaryParse | None:
    u1, u2 = (m.group("u1") or "").lower(), (m.group("u2") or "").lower()
    if u1 in _BIG or u2 in _BIG:
        return None
    markers = [x for x in (m.group("c1"), m.group("c2"), m.group("c3")) if x]
    # An explicit code anywhere in the figure ("$104,800—$176,400 USD") wins over an ambiguous "$".
    marker = next((x for x in markers if fx.identify(x)), markers[0] if markers else None)
    indian_unit = any(u in ("lpa", "lakh", "lakhs", "lac", "lacs", "cr", "crore", "crores") for u in (u1, u2))
    if (u1 == "l" or u2 == "l") and not (marker and fx.identify(marker) == "INR"):
        return None  # a bare "L" is only a lakh next to a rupee sign
    is_range = m.group("n2") is not None
    if (
        is_range
        and (m.group("sep") or "").lower() == "and"
        and not re.search(r"between\s*$", text[max(0, m.start() - 12) : m.start()], re.I)
    ):
        return None  # "₹5,000 and ₹10,000" lists two amounts; only "between X and Y" is a range
    # Units written once apply to both ends ("6-9 lakh", "C$90k-110k").
    mu1, mu2 = _MULT.get(u1, 1.0), _MULT.get(u2, 1.0)
    if is_range and not u1 and u2:
        mu1 = mu2
    if is_range and u1 and not u2:
        mu2 = mu1
    lo = _num(m.group("n1")) * mu1
    hi = _num(m.group("n2")) * mu2 if is_range else lo
    if hi < lo:
        return None
    period = _period(text, m.start(), m.end(), u2 or u1)
    window = text[max(0, m.start() - 60) : m.start()]
    sentence = text[max(0, m.start() - 60) : m.end() + 30]
    pay_context = bool(_PAY_WORDS.search(window)) or bool(_PAY_WORDS.search(sentence))
    # Accept: an explicit currency or Indian pay unit, plus either a range, a stated period or pay wording.
    # Without a currency, only "30k/month"-style amounts next to pay wording are accepted.
    if marker or indian_unit:
        if not (is_range or period or pay_context or indian_unit):
            return None
    elif not (pay_context and period):
        return None  # no currency: only an amount with pay wording and an explicit period counts
    if period is None and hi < 1000:
        return None  # "$5 - $10 off" is not pay
    if hi > _MAX_PLAUSIBLE[period]:
        return None
    currency = fx.identify(marker) if marker else ("INR" if indian_unit else None)
    if marker and currency is None and marker.strip() in fx.AMBIGUOUS:
        currency = "$"
    return SalaryParse(lo, hi, currency, period, m.group(0).strip())


def find_salaries(text: str) -> list[SalaryParse]:
    """Every pay figure stated in a text, in order (used to compare with structured pay)."""
    out: list[SalaryParse] = []
    for m in _AMOUNT.finditer(text or ""):
        if m.group("n1") and (c := _candidate(text, m)) is not None:
            out.append(c)
    return out


def parse_salary_text(text: str) -> SalaryParse:
    """The first pay figure in a text. Currency and period are taken only from what the text states."""
    found = find_salaries(text)
    return found[0] if found else SalaryParse()


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
