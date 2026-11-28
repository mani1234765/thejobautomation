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


# ── Sections and sentences ─────────────────────────────────────────────────────────────────────
# A listing is split into sections by its headings. A section is MANDATORY (requirements), PREFERRED (nice to
# have), IGNORED (about the company, benefits, equal-opportunity and application boilerplate) or MENTIONED
# (responsibilities, overview, and any text before the first heading). Every extractor below reads the same
# segments, so boilerplate is ignored consistently. Wording inside a sentence ("X is required", "Y is a plus")
# overrides the section it sits in.
MANDATORY, PREFERRED, MENTIONED, IGNORED = "mandatory", "preferred", "mentioned", "ignored"
_TIER_RANK = {MANDATORY: 3, PREFERRED: 2, MENTIONED: 1}

_LEAD = r"[^\w]*(?:(?:the|your|our|some|key|core|basic|minimum|main|additional|other|job|role|technical)\s+)*"
_HEADINGS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (tier, re.compile(_LEAD + f"(?:{pat})", re.I))
    for tier, pat in (
        (MANDATORY, r"about you\b"),
        (MENTIONED, r"about (?:the |this )?(?:role|job|position|opportunity|team)\b"),
        (
            PREFERRED,
            r"(?:nice|good|great)[- ]to[- ]haves?\b|preferred\b|desired\b|desirable\b|bonus\b|pluses\b|extra credit\b|"
            r"it'?s a plus\b|(?:even )?better if\b|ideally\b|we'?d love\b",
        ),
        (
            IGNORED,
            r"about\b|who we are\b|story\b|mission\b|values\b|culture\b|company\b|why (?:join|work|you'?ll love)\b|"
            r"life at\b|benefits\b|perks\b|what we offer\b|what'?s in it for you\b|compensation\b|salary\b|"
            r"pay (?:range|transparency)\b|total rewards\b|equal (?:employment )?opportunit|eeo\b|diversity\b|"
            r"inclusion\b|accommodations?\b|how to apply\b|(?:application|hiring|interview|recruitment) process\b|"
            r"privacy\b|disclaimer\b|commitment\b|additional information\b",
        ),
        (
            MANDATORY,
            r"requirements?\b|required\b|qualifications\b|what (?:you|we)(?:'ll| will)? (?:need|bring|have|require|expect)\b|"
            r"what we(?:'re| are) looking for\b|who you are\b|you (?:have|bring|are|will have|should have|must have|'ll need|need)\b|"
            r"must[- ]haves?\b|skills\b|experience\b|eligibility\b|(?:the )?ideal candidate\b|candidate profile\b|"
            r"profile\b|background\b",
        ),
        (
            MENTIONED,
            r"responsibilities\b|what you(?:'ll| will)? (?:do|be doing|work on)\b|role\b|overview\b|job description\b|"
            r"description\b|impact\b|day[- ]to[- ]day\b|in this role\b|tech(?:nology)? stack\b|location\b|team\b",
        ),
    )
)
_BOILERPLATE = re.compile(
    r"equal (?:employment )?opportunity|without regard to|regardless of (?:race|gender|age|religion)|"
    r"do(?:es)? not discriminate|reasonable accommodations?|e-verify|privacy (?:policy|notice)|"
    r"protected (?:veteran|characteristic|class)",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:[•\-*–·▪◦]|\d{1,2}[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(“\"•])|;\s*")


@dataclass
class Segment:
    text: str  # one sentence (or bullet) of the listing
    section: str  # MANDATORY | PREFERRED | MENTIONED | IGNORED
    heading: str | None = None


def _heading(line: str) -> tuple[str, str, str] | None:
    """(tier, heading, remaining text) when a line is a section heading ("Requirements", "Nice to have:",
    "Must have: Figma")."""
    for tier, pattern in _HEADINGS:
        m = pattern.match(line)
        if not m:
            continue
        rest = line[m.end() :]
        inline = re.match(r"\s*[^\w\s(]*?\s*[:\-–—]\s*", rest)
        if inline and rest.strip()[:1] in ":-–—":
            return tier, line[: m.end()].strip(" :-–—"), rest[inline.end() :]
        words = re.findall(r"[\w']+", rest)
        if len(words) <= 3 and len(line) <= 70 and not line.rstrip().endswith("."):
            return tier, line.strip(" :-–—?"), ""
        return None
    return None


def segments(text: str) -> list[Segment]:
    """The listing as sentences, each tagged with the section it belongs to."""
    out: list[Segment] = []
    tier, heading, bullets_seen = MENTIONED, None, False
    for raw in (text or "").replace("’", "'").splitlines():
        line = raw.strip()
        if not line:
            continue
        is_bullet = bool(_BULLET.match(line))
        body = _BULLET.sub("", line, count=1)
        if not is_bullet and (h := _heading(body)):
            tier, heading, body = h[0], h[1], h[2]
            bullets_seen = False
            if not body.strip():
                continue
        elif (
            tier in (MANDATORY, PREFERRED)
            and bullets_seen
            and not is_bullet
            and len(body) > 100
            and not _cue_matches(body)
        ):
            # A plain paragraph after a list usually closes it ("We're a remote-first team founded in …").
            tier, heading = MENTIONED, None
        bullets_seen = bullets_seen or is_bullet
        start = 0
        for m in [*_SENTENCE_END.finditer(body), None]:
            end = m.start() if m else len(body)
            sentence = body[start:end].strip()
            if m:
                start = m.end()
            if not sentence:
                continue
            section = IGNORED if _BOILERPLATE.search(sentence) else tier
            out.append(Segment(sentence, section, heading))
    return out


def _quote(sentence: str, limit: int = 180) -> str:
    s = re.sub(r"\s+", " ", sentence).strip(" .•-")
    return f"“{s if len(s) <= limit else s[: limit - 1].rstrip() + '…'}”"


# Sentence-level wording. Forward cues apply to what follows ("Must have: X"), backward cues to what precedes
# ("X is required"). A negated requirement ("X is not required") makes X merely mentioned.
_CUES: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (tier, direction, re.compile(pat, re.I))
    for tier, direction, pat in (
        (
            MANDATORY,
            "fwd",
            r"\bmust[- ]haves?\b|\bmust (?:have|know|be (?:proficient|experienced|skilled|familiar|fluent)(?: in| with)?)\b|"
            r"\bneed to (?:have|know)\b|\brequired(?: skills?| experience| qualifications)?\s*[:\-–]|"
            r"\brequirements?\s*[:\-–]|\bmandatory\s*[:\-–]|\bessential\s*[:\-–]",
        ),
        (
            MANDATORY,
            "bwd",
            r"\b(?:is|are) (?:a )?(?:required|mandatory|essential|must)\b|\brequired\b(?! to\b)|\bmandatory\b|"
            r"\bessential\b|\ba must\b",
        ),
        (
            PREFERRED,
            "fwd",
            r"\b(?:nice|good|great)[- ]to[- ]haves?\s*[:\-–]|\bbonus(?: points)?(?: for| if)?\s*[:\-–]|"
            r"\bbonus points (?:for|if)\b|\bpreferred\s*[:\-–]|\bideally\b|\bpreferably\b|"
            r"\bit(?:'s| is| would be) (?:a plus|nice|great|helpful) (?:if|to)\b|\bplus\s*[:\-–]|\bdesirable\s*[:\-–]",
        ),
        (
            PREFERRED,
            "bwd",
            r"\b(?:a|big|huge|definite|strong|nice) plus\b|\bpluses\b|\bpreferred\b|\bpreferable\b|\bdesirable\b|"
            r"\bdesired\b|\b(?:nice|good|great)[- ]to[- ]have\b|\badvantage(?:ous)?\b|\bbonus\b|\bhelpful\b|\bwelcome\b",
        ),
    )
)
_NEGATED = re.compile(r"\b(?:not|no|isn't|aren't|never|nor)\b[\w\s']{0,12}$", re.I)


@dataclass
class _Cue:
    tier: str
    direction: str
    start: int
    end: int


def _cue_matches(sentence: str) -> list[_Cue]:
    cues: list[_Cue] = []
    for tier, direction, pattern in _CUES:
        for m in pattern.finditer(sentence):
            if any(c.start <= m.start() < c.end or m.start() <= c.start < m.end() for c in cues):
                continue  # a forward cue ("Nice to have:") already covers this wording
            t = tier
            if tier == MANDATORY and _NEGATED.search(sentence[: m.start()]):
                t = MENTIONED
            d = "fwd" if direction == "bwd" and m.start() <= 2 else direction  # "Preferred Go experience"
            cues.append(_Cue(t, d, m.start(), m.end()))
    return cues


def _cue_tier(cues: list[_Cue], start: int, end: int) -> str | None:
    """The tier the sentence's wording gives to the text at [start, end): the nearest applicable cue."""
    fwd = [c for c in cues if c.direction == "fwd" and c.end <= start]
    bwd = [c for c in cues if c.direction == "bwd" and c.start >= end]
    best: tuple[int, str] | None = None
    if fwd:
        c = max(fwd, key=lambda c: c.end)
        best = (start - c.end, c.tier)
    if bwd:
        c = min(bwd, key=lambda c: c.start)
        if best is None or c.start - end < best[0]:
            best = (c.start - end, c.tier)
    return best[1] if best else None


# ── Skills ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class SkillSplit:
    required: list[str] = field(default_factory=list)  # mandatory
    preferred: list[str] = field(default_factory=list)
    mentioned: list[str] = field(default_factory=list)
    # canonical name → {"tier", "term" (as written), "quote", "section"}
    evidence: dict[str, dict[str, str | None]] = field(default_factory=dict)


def split_skills(text: str, segs: list[Segment] | None = None) -> SkillSplit:
    """Classify each skill as mandatory, preferred or merely mentioned, with the sentence that decided it.

    Skills in requirement sections are mandatory; in nice-to-have sections preferred; elsewhere (responsibilities,
    overview, a listing without headings) only mentioned. Sentence wording overrides the section. About-us,
    benefits and equal-opportunity text is ignored. Soft skills are never mandatory (at most preferred)."""
    best: dict[str, tuple[int, dict[str, str | None]]] = {}
    for seg in segs if segs is not None else segments(text):
        if seg.section == IGNORED:
            continue
        cues = _cue_matches(seg.text)
        for hit in taxonomy.find_skills(seg.text, listing=True):
            tier = _cue_tier(cues, hit.start, hit.end) or seg.section
            if tier == MANDATORY and taxonomy.category_of(hit.name) == "soft":
                tier = PREFERRED
            rank = _TIER_RANK[tier]
            if hit.name not in best or rank > best[hit.name][0]:
                best[hit.name] = (
                    rank,
                    {"tier": tier, "term": hit.term, "quote": _quote(seg.text), "section": seg.heading},
                )
    out = SkillSplit()
    for name, (_, ev) in best.items():
        {MANDATORY: out.required, PREFERRED: out.preferred, MENTIONED: out.mentioned}[ev["tier"]].append(name)  # type: ignore[index]
        out.evidence[name] = ev
    return out


# ── Compensation wording ──────────────────────────────────────────────────────────────────────
_STIPEND_WORD = re.compile(r"\bstipends?\b", re.I)
_SALARY_WORD = re.compile(r"\b(?:salary|salaries|ctc|cost to company|wages?)\b", re.I)


def compensation_wording(text: str, pay_text: str | None = None) -> dict[str, str]:
    """{"stipend" | "salary": quote} for each way the listing explicitly names its pay (boilerplate ignored).
    Only words count; a monthly or annual amount alone says nothing about which it is."""
    found: dict[str, str] = {}
    sentences = [s.text for s in segments(text) if s.section != IGNORED]
    for sentence in [*sentences, *([pay_text] if pay_text else [])]:
        for kind, pattern in (("stipend", _STIPEND_WORD), ("salary", _SALARY_WORD)):
            if kind not in found and pattern.search(sentence):
                found[kind] = _quote(sentence)
    return found


# ── Employment type ────────────────────────────────────────────────────────────────────────────
EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "internship", "freelance", "temporary", "apprenticeship")
_HOURS_TYPES = {"full_time", "part_time"}  # describe hours; another type can be full- or part-time too
_HINTS = {
    "fulltime": "full_time",
    "permanent": "full_time",
    "regular": "full_time",
    "parttime": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "contracttohire": "contract",
    "fixedterm": "contract",
    "intern": "internship",
    "internship": "internship",
    "freelance": "freelance",
    "freelancer": "freelance",
    "temporary": "temporary",
    "temp": "temporary",
    "seasonal": "temporary",
    "apprentice": "apprenticeship",
    "apprenticeship": "apprenticeship",
}
_TITLE_TYPES = (
    ("internship", r"\bintern(?:ship)?s?\b"),
    ("apprenticeship", r"\bapprentice(?:ship)?s?\b"),
    ("freelance", r"\bfreelance(?:r)?\b"),
    ("temporary", r"\btemp(?:orary)?\b|\bseasonal\b"),
    ("contract", r"\bcontract(?:or)?\b|\bc2h\b|\bfixed[- ]term\b"),
    ("part_time", r"\bpart[- ]?time\b"),
    ("full_time", r"\bfull[- ]?time\b|\bpermanent\b"),
)
_TEXT_TYPES = tuple(
    (t, re.compile(p, re.I))
    for t, p in (
        ("internship", r"\binternships?\b|\bintern\b"),
        ("apprenticeship", r"\bapprentice(?:ship)?s?\b"),
        (
            "freelance",
            # "freelance role", "as a freelancer"; not "manage agencies and freelancers"
            r"\bfreelance (?:role|position|basis|engagement|project|contract|opportunity|job|assignment|gig|work)\b|"
            r"\b(?:is|on|as) an? freelance\b|\b(?:as|is|for|hiring|seeking|looking for)\s+(?:an?\s+)?(?:[\w-]+\s+)?freelancers?\b",
        ),
        (
            "temporary",
            r"\btemporary (?:role|position|job|assignment|employment|basis|contract|hire|staff|worker)\b|"
            r"\btemp (?:role|position|job|staff|worker)\b|\bseasonal (?:role|position|job|hire|staff|worker|employment)\b|"
            r"\b(?:maternity|parental|paternity) (?:leave )?cover\b|\b(?:role|position|job) is temporary\b",
        ),
        (
            "contract",
            r"\bcontract[- ]to[- ](?:hire|perm(?:anent)?)\b|\bc2h\b|\b(?:as|is|for|hiring|seeking) an? (?:[\w-]+ )?contractor\b|"
            r"\bcontractual (?:role|position|basis|employment|job)\b|\bfixed[- ]term\b|"
            r"\b(?:on|for) a (?:\d+[- ](?:month|week|year)s? )?contract(?: basis)?\b|"
            r"\bcontract (?:role|position|basis|opportunity|engagement|assignment|job|employment|hire)\b|"
            r"\b\d+[- ](?:month|week|year)s? contract\b|\bcontract (?:duration|length|term|period)\b",
        ),
        ("part_time", r"\bpart[- ]?time\b"),
        (
            "full_time",
            r"\bfull[- ]?time\b|\bpermanent (?:role|position|job|employment|contract|hire|opportunity)\b|"
            r"\bpermanent,|\bregular (?:full[- ]time )?(?:employee|employment|role|position)\b",
        ),
    )
)
# Wording that names a type without describing this role ("conversion to full-time", "part-time MBA").
_NOT_THIS_ROLE = re.compile(
    r"(?:convert(?:ed|ing|s)?|conversion|transition(?:ing)?|lead(?:s|ing)?|path|move|possibility|potential|"
    r"opportunity|option|chance|offer)\b[^.;\n]{0,25}?\b(?:full[- ]?time|permanent)\b(?: (?:role|position|employment|offer|job|hire))?|"
    r"\bfull[- ]?time (?:equivalent|students?|degree|mba|program(?:me)?|course|stud(?:y|ies))\b|"
    r"\bpart[- ]?time (?:mba|degree|students?|stud(?:y|ies)|course|program(?:me)?)\b",
    re.I,
)
_TYPE_WORD = r"full[- ]?time|part[- ]?time|permanent|contract(?:or)?|freelance|temporary|internship"
_ALTERNATIVES = re.compile(rf"\b({_TYPE_WORD})\s*(?:,\s*)?(?:or|/|and/or)\s*({_TYPE_WORD})\b", re.I)
_TYPE_LABEL = re.compile(
    r"\b(?:employment|job|position|contract|work|role|engagement) type\s*[:\-–]\s*([A-Za-z][\w -]{1,30})", re.I
)


@dataclass
class EmploymentFacts:
    value: str | None
    is_internship: bool
    evidence: str | None = None
    note: str | None = None  # why it is unknown, when the listing contradicts itself


def _hint_type(hint: str | None) -> str | None:
    h = re.sub(r"[^a-z]", "", (hint or "").lower())
    if h in _HINTS:
        return _HINTS[h]
    for key in ("intern", "apprentice", "freelance", "temporary", "contract", "parttime", "fulltime", "permanent"):
        if key in h:
            return _HINTS[key]
    return None


def detect_employment(
    title: str, hint: str | None, text: str = "", segs: list[Segment] | None = None
) -> EmploymentFacts:
    """Employment type from, in order: the board's structured field, the title, an explicit "Employment type:"
    line, then the description's wording. Contradictory wording ("full-time or part-time") stays unknown."""
    if (h := _hint_type(hint)) is not None:
        return EmploymentFacts(h, h == "internship", f"Board field: “{hint}”")
    for t, p in _TITLE_TYPES:
        if re.search(p, title, re.I):
            return EmploymentFacts(t, t == "internship", f"Title: “{title}”")
    found: dict[str, str] = {}  # type → quote
    for seg in segs if segs is not None else segments(text):
        if seg.section == IGNORED:
            continue
        if (m := _TYPE_LABEL.search(seg.text)) and (lt := _hint_type(m.group(1))):
            return EmploymentFacts(lt, lt == "internship", _quote(seg.text))
        sentence = _NOT_THIS_ROLE.sub(" ", seg.text)
        alt = _ALTERNATIVES.search(sentence)
        if alt and _hint_type(alt.group(1)) != _hint_type(alt.group(2)):
            return EmploymentFacts(None, False, _quote(seg.text), "The listing offers more than one type.")
        hits = [(t, m) for t, p in _TEXT_TYPES for m in p.finditer(sentence)]
        types = {t for t, _ in hits}
        if len(types) > 1:
            spans = sorted((m.start(), m.end(), t) for t, m in hits)
            for (_, e1, t1), (s2, _, t2) in zip(spans, spans[1:], strict=False):
                if t1 != t2 and re.fullmatch(r"\s*(?:,\s*)?(?:or|/|and/or)\s*", sentence[e1:s2], re.I):
                    return EmploymentFacts(None, False, _quote(seg.text), "The listing offers more than one type.")
        for t in types:
            found.setdefault(t, _quote(seg.text))
    kinds = set(found) - _HOURS_TYPES if set(found) - _HOURS_TYPES else set(found)
    if len(kinds) == 1:
        t = kinds.pop()
        return EmploymentFacts(t, t == "internship", found[t])
    if kinds:
        return EmploymentFacts(
            None, False, " / ".join(found[k] for k in sorted(kinds)), "The listing states conflicting types."
        )
    return EmploymentFacts(None, False)


def employment_type(title: str, hint: str | None, text: str = "") -> tuple[str | None, bool]:
    e = detect_employment(title, hint, text)
    return e.value, e.is_internship


# ── Experience ─────────────────────────────────────────────────────────────────────────────────
_YRS = r"(?:years?|yrs?)\b"
_N = r"(\d{1,2}(?:\.\d)?)"
_EXP_RANGE = re.compile(rf"{_N}\s*\+?\s*(?:-|–|—|to)\s*{_N}\s*\+?\s*{_YRS}", re.I)
_EXP_MIN = re.compile(
    rf"(?:(?:at least|minimum(?: of)?|min\.?|over|more than)\s+)?{_N}\s*(?:\+|plus)?\s*\+?\s*{_YRS}", re.I
)
_EXP_AFTER = re.compile(
    r"^(?:'s)?\s*(?:of\s+)?(?:(?:professional|relevant|industry|hands-on|practical|proven|commercial|prior|total|"
    r"overall|work(?:ing)?|full[- ]time)\s+)*(?:experience|exp\b)|^(?:'s)?\s*(?:of\s+)?(?:[\w/+#.-]+\s+){0,4}?experience\b|"
    r"^\s*(?:of\s+)?(?:hands-on\s+|professional\s+)?(?:building|developing|working|writing|designing|leading|managing|"
    r"programming|coding|shipping|running|operating|delivering|engineering|as an?\b|in (?:an? |the )?[\w-]+ "
    r"(?:role|position|environment|team|industry|setting|capacity))",
    re.I,
)
_EXP_BEFORE = re.compile(
    r"(?:experience|exp\.?)(?:\s+(?:required|level|needed|range))?\s*[:\-–]\s*$|experience of\s*$", re.I
)
_NOT_EXPERIENCE_BEFORE = re.compile(
    r"\b(?:past|last|the|within|next|first|founded|since|around for|operating for|been|in business for|for over)\s*$",
    re.I,
)
_NOT_EXPERIENCE_AFTER = re.compile(
    r"^\s*(?:ago|old|young|running|in business|of age|of (?:growth|history|operations?|existence|business|service|"
    r"innovation|excellence))\b",
    re.I,
)
_SUB_REQUIREMENT = re.compile(r"(?:including|incl\.?|of which|out of which|among which|with)\s+(?:at least\s+)?$", re.I)
_NO_EXPERIENCE = re.compile(
    r"\bfreshers?\b(?! (?:are )?not)|\bno (?:prior |previous |professional |work )?experience (?:is )?"
    r"(?:required|necessary|needed)\b|\bentry[- ]level\b|\bnew grad(?:uate)?s?\b|\brecent graduates?\b",
    re.I,
)


@dataclass
class ExperienceFacts:
    minimum: float | None = None  # mandatory
    maximum: float | None = None
    preferred_minimum: float | None = None  # "3+ years preferred": never treated as a requirement
    evidence: str | None = None
    preferred_evidence: str | None = None
    stated_minimums: list[float] = field(default_factory=list)  # every primary mandatory statement


def _accept_years(sentence: str, m: re.Match[str], section: str) -> bool:
    before, after = sentence[max(0, m.start() - 40) : m.start()], sentence[m.end() : m.end() + 70]
    if _NOT_EXPERIENCE_BEFORE.search(before) or _NOT_EXPERIENCE_AFTER.match(after):
        return False
    if _EXP_AFTER.match(after) or _EXP_BEFORE.search(before):
        return True
    if re.match(r"\s*of\s+", after) and taxonomy.find_skills(after[:40], listing=True):
        return True  # "3+ years of Python"
    if section in (MANDATORY, PREFERRED) and re.match(r"\s*in\s+\w", after):
        return True  # "5+ years in product management" under a requirements heading
    if re.search(r"\bexperienc\w*\b[^.;]{0,80}\bfor\s*$", sentence[: m.start()], re.I):
        return True  # "Experience operating Kubernetes in production for at least 4 years"
    stated = section in (MANDATORY, PREFERRED) or re.search(
        r"\b(?:must have|requires?|need(?:s)?|with)\s*$", sentence[: m.start()], re.I
    )
    if stated and re.match(r"\s*(?:of\s+)?[a-z]+ing\b", after, re.I):
        return True  # "Must have 2 years supervising warehouse staff"
    rest = (sentence[: m.start()] + sentence[m.end() :]).strip(" .,:;-–•")
    # A bare "• 2 to 4 years" under a requirements heading, or "Ideally 5+ years." / "4+ years preferred."
    return (section in (MANDATORY, PREFERRED) or bool(_cue_matches(sentence))) and len(rest.split()) <= 2


def experience(text: str, segs: list[Segment] | None = None) -> ExperienceFacts:
    """Years of experience: mandatory (min, max) and a separate preferred minimum, with the quoted sentence.
    Company history ("founded 15 years ago"), version numbers and sub-requirements ("including 2 years managing")
    are not requirements. "Freshers welcome" / "No prior experience required" mean a minimum of 0 with no maximum."""
    out = ExperienceFacts()
    mandatory: list[tuple[float, float | None, str]] = []
    no_experience: str | None = None
    for seg in segs if segs is not None else segments(text):
        if seg.section == IGNORED:
            continue
        s = seg.text
        cues = _cue_matches(s)
        found: list[tuple[re.Match[str], float, float | None]] = []
        for m in _EXP_RANGE.finditer(s):
            lo, hi = float(m.group(1)), float(m.group(2))
            if lo < hi <= 30 and _accept_years(s, m, seg.section):
                found.append((m, lo, hi))
        for m in _EXP_MIN.finditer(s):
            if any(f.start() <= m.start(1) < f.end() for f, _, _ in found):
                continue
            v = float(m.group(1))
            if v <= 30 and _accept_years(s, m, seg.section):
                found.append((m, v, None))
        for m, lo, hi in sorted(found, key=lambda f: f[0].start()):
            tier = _cue_tier(cues, m.start(), m.end()) or seg.section
            if tier == MENTIONED and any(c.tier == MENTIONED for c in cues):
                continue  # "experience is not required"
            if tier == PREFERRED:
                if out.preferred_minimum is None:
                    out.preferred_minimum, out.preferred_evidence = lo, _quote(s)
                continue
            if _SUB_REQUIREMENT.search(s[: m.start()]) and mandatory:
                continue  # "8 years, including 2 years managing teams"
            mandatory.append((lo, hi, _quote(s)))
        if no_experience is None and _NO_EXPERIENCE.search(s):
            no_experience = _quote(s)
    if mandatory:
        out.minimum, out.maximum, out.evidence = mandatory[0]
        out.stated_minimums = sorted({lo for lo, _, _ in mandatory})
    elif no_experience:
        out.minimum, out.evidence = 0.0, no_experience
    return out


def experience_range(text: str) -> tuple[float | None, float | None]:
    x = experience(text)
    return x.minimum, x.maximum


# ── Work arrangement ───────────────────────────────────────────────────────────────────────────
_MODE_CUES = tuple(
    (mode, re.compile(p, re.I))
    for mode, p in (
        (
            "onsite",
            r"\bon[- ]?site\b|\bin[- ]office\b|\bin the office\b|\boffice[- ]based\b|\bwork(?:ing)? from (?:the |our )?office\b|"
            r"\bwfo\b|\b5 days (?:a|per|/) ?week (?:in|at|from) (?:the |our )?office\b|"
            r"\bwork(?:ing)? (?:from|at|in) (?:the |our )?(?:[\w-]+ ){0,2}office\b|\bbased (?:in|at|out of) (?:the |our )?(?:[\w-]+ ){0,2}office\b",
        ),
        (
            "hybrid",
            r"\bhybrid\b|\b[1-4] days? (?:a |per |/ ?)?(?:week )?(?:(?:in|at|from) (?:the |our )?office|wfo|work from office)\b|"
            # "onsite from our New York office three or more days per week": office days of a hybrid schedule
            r"\b(?:one|two|three|four|[1-4])(?: or more| to (?:two|three|four|[2-4]))? days? (?:a|per|/|each) ?week\b"
            r"(?=[^.;]*\b(?:office|on-?site|in[- ]person)\b)|(?<=office )(?:one|two|three|four|[1-4])(?: or more)? days? (?:a|per) week\b",
        ),
        (
            "remote",
            r"\b(?:fully |100% |completely )?remote\b|\bwork(?:ing)? from home\b|\bwfh\b|\bwork from anywhere\b|"
            r"\bwork(?:ing)? remotely\b|\bremotely from\b",
        ),
    )
)
# "remote troubleshooting", "on-site interview", "hybrid cloud": the word doesn't describe where the role is done.
_MODE_NOT_ARRANGEMENT = re.compile(
    r"^[- ]?(?:troubleshooting|sensing|monitoring|access|support|debugging|diagnostics|management|desktop|server|"
    r"control|patient|learning|interviews?|onboarding|hiring|tools?|work experience|experience|visits?|events?|"
    r"customers?|clients?|cloud|apps?|applications?|mobile|infrastructure|environments?|vehicles?|engines?|"
    r"workers|employees|colleagues|perks|benefits|amenities|lunch|meals|snacks|conversions?|rates?)\b",
    re.I,
)
# "meeting customers onsite", "travel to client sites on-site": about someone else's premises.
_MODE_ABOUT_OTHERS = re.compile(
    r"\b(?:customers?|clients?|partners?|users?|patients?|visit\w*|travel\w*)\s+(?:\w+\s+)?$", re.I
)
_COMPANY_LEVEL = re.compile(
    r"^[- ]?(?:first|friendly)\b|^\s*(?:team|teams|company|culture|organi[sz]ation|workforce|team members)\b", re.I
)
_ROLE_LEVEL = re.compile(
    r"\b(?:this|the) (?:role|position|job|opportunity)\b|\byou(?:'ll| will)? (?:work|be working|be based)\b|"
    r"\bwork(?:place)? (?:arrangement|mode|type|model|location|setup)\b|\blocation\s*:|\bthis is an?\b",
    re.I,
)
# Remote work on set days ("WFH allowed on Fridays", "remote 2 days a week") describes a hybrid schedule …
_PART_TIME_REMOTE = re.compile(
    r"^[\w\s']{0,15}?\b(?:on (?:mondays?|tuesdays?|wednesdays?|thursdays?|fridays?)|[1-4] days?|"
    r"(?:one|two|three|four) days?|once a week|twice a week|some days|flexib\w*)\b",
    re.I,
)
# … while occasional remote work ("occasional remote days", "WFH occasionally") doesn't change where the role is.
_OCCASIONAL = re.compile(r"\b(?:occasional(?:ly)?|from time to time|now and then|ad[- ]hoc|when needed)\b", re.I)
_MODE_NEGATED = re.compile(r"(?:\b(?:not|isn't|aren't|no|never|cannot|can't|won't)\b[\w\s'-]{0,15}|\bnon[- ]?)$", re.I)

_REGION_NAMES = {
    "india": "India",
    "united states": "United States",
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s": "United States",
    "united kingdom": "United Kingdom",
    "uk": "United Kingdom",
    "canada": "Canada",
    "germany": "Germany",
    "france": "France",
    "spain": "Spain",
    "portugal": "Portugal",
    "netherlands": "Netherlands",
    "ireland": "Ireland",
    "poland": "Poland",
    "europe": "Europe",
    "eu": "European Union",
    "emea": "EMEA",
    "apac": "APAC",
    "latam": "LATAM",
    "americas": "Americas",
    "north america": "North America",
    "latin america": "Latin America",
    "asia": "Asia",
    "australia": "Australia",
    "singapore": "Singapore",
    "japan": "Japan",
    "philippines": "Philippines",
    "brazil": "Brazil",
    "mexico": "Mexico",
    "uae": "UAE",
}
# Abbreviations must be upper case ("US", not "us").
_REGION = (
    r"(?:US|U\.S\.?|USA|UK|EU|EMEA|APAC|LATAM|UAE|(?i:india|united states|united kingdom|canada|germany|france|spain|"
    r"portugal|netherlands|ireland|poland|europe|americas|north america|latin america|asia|australia|singapore|japan|"
    r"philippines|brazil|mexico))"
)
_REGION_LIST = rf"{_REGION}(?:\s*(?:,|/|\bor\b|\band\b)\s*{_REGION})*"
_REGION_IN_TEXT = (
    re.compile(
        rf"(?i:remote(?:ly)?)\s*(?:\(|-|–|—|,|:)?\s*(?:(?i:only)\s+)?(?:(?i:within|in|from|across|based in|for)\s+)?"
        rf"(?:(?i:anywhere)\s+(?:(?i:in|within)\s+)?)?(?:(?i:the)\s+)?({_REGION_LIST})\b"
    ),
    re.compile(rf"({_REGION_LIST})[- ](?:(?i:based)\s+)?(?i:remote)\b"),
    re.compile(
        rf"(?i:must|should|need to)\s+(?i:be\s+)?(?i:based|located|resid\w*|living)\s+(?i:in)\s+(?:(?i:the)\s+)?({_REGION_LIST})\b"
    ),
    re.compile(
        rf"(?i:open to (?:candidates|applicants)) (?:(?i:based|located) )?(?i:in) (?:(?i:the) )?({_REGION_LIST})\b"
    ),
)


@dataclass
class WorkArrangement:
    mode: str | None = None  # remote | hybrid | onsite
    evidence: str | None = None
    region: str | None = None  # where a remote role may be done from ("India", "EMEA"); informational only
    region_evidence: str | None = None


def _regions(found: str) -> str:
    names = [
        _REGION_NAMES.get(r.lower().strip(), r.strip())
        for r in re.split(r"\s*(?:,|/|\bor\b|\band\b)\s*", found)
        if r.strip()
    ]
    return ", ".join(dict.fromkeys(names))


def work_arrangement(location: str | None, text: str = "", segs: list[Segment] | None = None) -> WorkArrangement:
    """Remote, hybrid or on-site, from statements about this role. Negations ("not remote") rule a mode out;
    adjective uses ("remote troubleshooting") and company-level wording ("remote-first company") count only when
    nothing describes the role itself. Statements about the role beat the location field, which beats generic
    wording. Equally strong statements that disagree leave it unknown."""
    statements: list[tuple[float, str, str]] = []  # (strength, mode, quote); quote identifies the sentence
    negated: set[str] = set()
    sources: list[tuple[str, float, bool]] = []  # (sentence, base strength, is_location)
    if location:
        sources.append((location, 2.5, True))
    for seg in segs if segs is not None else segments(text):
        if seg.section != IGNORED:
            sources.append((seg.text, 3.0 if _ROLE_LEVEL.search(seg.text) or len(seg.text) <= 40 else 2.0, False))
    for sentence, strength, is_location in sources:
        # Longest wording first, so "3 days a week in office" (hybrid) isn't also read as "in office" (on-site).
        cues = sorted(
            ((mode, m) for mode, pattern in _MODE_CUES for m in pattern.finditer(sentence)),
            key=lambda c: c[1].start() - c[1].end(),
        )
        taken: list[tuple[int, int]] = []
        for mode, m in cues:
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append(m.span())
            if _MODE_NEGATED.search(sentence[max(0, m.start() - 25) : m.start()]):
                negated.add(mode)
                continue
            after = sentence[m.end() : m.end() + 30]
            if _MODE_NOT_ARRANGEMENT.match(after) or _MODE_ABOUT_OTHERS.search(
                sentence[max(0, m.start() - 30) : m.start()]
            ):
                continue
            st, md = strength, mode
            if mode == "remote" and (
                _COMPANY_LEVEL.match(after)
                or re.search(
                    r"\b(?:we are|we're|our)\s+(?:a\s+)?(?:fully\s+)?$",
                    sentence[max(0, m.start() - 20) : m.start()],
                    re.I,
                )
            ):
                st = 1.0  # "remote-first company", "we're fully remote": about the company, not this role
            if mode == "remote" and (
                _OCCASIONAL.search(sentence[max(0, m.start() - 20) : m.start()])
                or _OCCASIONAL.search(sentence[m.end() : m.end() + 25])
            ):
                continue  # "Occasional remote work is possible": not this role's arrangement
            if mode == "remote" and _PART_TIME_REMOTE.match(after):
                md = "hybrid"
            quote = f"Location: “{sentence}”" if is_location else _quote(sentence)
            statements.append((st, md, quote))
    statements = [s for s in statements if s[1] not in negated]
    out = WorkArrangement()
    if statements:
        top = max(s[0] for s in statements)
        modes = {s[1] for s in statements if s[0] == top}
        same_sentence = len({s[2] for s in statements if s[0] == top}) == 1
        if modes == {"hybrid", "onsite"} and same_sentence:
            modes = {"hybrid"}  # "Hybrid: Tue–Thu in office" names the office days of a hybrid schedule
        if len(modes) == 1:
            out.mode = modes.pop()
            out.evidence = next(s[2] for s in statements if s[0] == top)
    if out.mode == "remote":
        for sentence, _, is_location in sources:
            for pattern in _REGION_IN_TEXT:
                if (m := pattern.search(sentence)) and m.group(1):
                    out.region = _regions(m.group(1))
                    out.region_evidence = f"Location: “{sentence}”" if is_location else _quote(sentence)
                    break
            if out.region:
                break
    return out


def work_mode(*hints: str | None) -> str | None:
    """The work arrangement stated in a location and/or text."""
    location, *rest = [*hints, None]
    return work_arrangement(location, " ".join(h for h in rest if h)).mode
