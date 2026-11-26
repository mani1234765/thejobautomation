"""Explainable eligibility verdicts.

Each job gets ELIGIBLE, REJECTED or MANUAL_REVIEW from independent checks. Every check records:
the rule that applied, a reason code, a plain-language explanation, evidence quoted from the listing,
and the information that was missing.

| Rule       | Rejects when …                                                     | Needs review when …                                                 |
|------------|--------------------------------------------------------------------|---------------------------------------------------------------------|
| SALARY     | verified minimum annual CTC is not strictly above your threshold    | pay is advertised-only, currency unknown/ambiguous, no fresh FX rate |
| STIPEND    | verified stipend is below your minimum (internships only)            | advertised-only, currency unknown, no fresh FX rate                  |
| SKILLS     | the listing calls ≥2 skills mandatory ("must have" …) and you have none | no overlap with the extracted skills, under half covered, or some mandatory skills missing |
| EXPERIENCE | you are more than 2 years short of the stated minimum                | 1–2 years short, or the listing states conflicting requirements      |
| SENIORITY  | the title's seniority implies >3 more years than you have            | 2–3 years more, or the title conflicts with the stated experience    |
| LOCATION   | work arrangement or city (by canonical alias) is outside your preferences | contradictory listing, or you have a preference it doesn't state     |
| EMPLOYMENT | the employment type isn't one you selected                           | you restrict employment type and the listing doesn't state it        |

Missing information never counts as a pass and never rejects on its own. When you have a preference for the
missing fact (location, work arrangement, employment type) the check needs review; otherwise it is
reported as `unknown`. Either way it is listed under `missing_information`. The verdict is REJECTED if any check rejects,
otherwise MANUAL_REVIEW if any check needs review, otherwise ELIGIBLE.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

from app.domain import fx
from app.domain import job_parsing as jp
from app.domain.locations import mentions_city
from app.domain.salary_policy import SalaryPolicy, evaluate

if TYPE_CHECKING:
    from app.domain.matching import CandidateProfile, JobFacts

Verdict = Literal["ELIGIBLE", "REJECTED", "MANUAL_REVIEW"]
Outcome = Literal["pass", "reject", "review", "unknown"]
LEGACY_STATUS = {"ELIGIBLE": "eligible", "REJECTED": "ineligible", "MANUAL_REVIEW": "review"}

RULES = {
    "SALARY": "Verified minimum annual CTC must be strictly greater than your threshold; undisclosed pay is "
    "eligible; advertised-only pay, unknown or ambiguous currency, or no fresh exchange rate → manual review.",
    "STIPEND": "Internship stipends are judged separately against your minimum monthly stipend.",
    "SKILLS": "Rejected only if the listing explicitly calls 2+ skills mandatory and you have none of them; "
    "review if no extracted skill matches, you cover under half, or a mandatory skill is missing.",
    "EXPERIENCE": "Rejected if you are more than 2 years short of the stated minimum; review if 1–2 years short or "
    "the listing is contradictory.",
    "SENIORITY": "Rejected if the title's seniority implies more than 3 years beyond yours; review if 2–3 years, or "
    "if the title conflicts with the stated experience.",
    "LOCATION": "Rejected if the work arrangement or city (compared by canonical name, e.g. Bangalore = "
    "Bengaluru) is outside your preferences; review if the listing contradicts itself or doesn't state "
    "something you have a preference for.",
    "EMPLOYMENT": "Rejected if the employment type isn't one you selected; review if you restrict employment "
    "type and the listing doesn't state it.",
}
# Minimum years a title's seniority usually implies.
SENIORITY_YEARS = {"intern": 0.0, "junior": 0.0, "mid": 2.0, "senior": 5.0, "staff+": 8.0}


@dataclass
class Check:
    rule: str
    outcome: Outcome
    code: str
    explanation: str
    evidence: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def quote(text: str, pattern: str, limit: int = 180) -> str | None:
    """The sentence (or line) of the listing that matches `pattern`, trimmed for display."""
    if not text:
        return None
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    start = max(text.rfind("\n", 0, m.start()), text.rfind(". ", 0, m.start()) + 1, 0)
    ends = [i for i in (text.find("\n", m.end()), text.find(". ", m.end())) if i != -1]
    end = min(ends) if ends else len(text)
    snippet = re.sub(r"\s+", " ", text[start:end]).strip(" .•-")
    if len(snippet) > limit:
        centre = m.start() - start
        lo = max(0, centre - limit // 2)
        snippet = ("…" if lo else "") + snippet[lo : lo + limit].strip() + "…"
    return f"“{snippet}”"


# ── Checks ─────────────────────────────────────────────────────────────────────────────────────
def _salary(job: JobFacts, policy: SalaryPolicy, fxc: fx.FxContext | None) -> Check:
    d = evaluate(job.salary, policy, fxc)
    outcome: Outcome = {"eligible": "pass", "ineligible": "reject", "manual_review": "review"}[d.decision]
    s = job.salary
    evidence: list[str] = []
    if s.text:
        evidence.append(f"“{s.text}”")
    elif s.minimum is not None or s.maximum is not None:
        rng = "–".join(f"{v:,.0f}" for v in (s.minimum, s.maximum) if v is not None)
        evidence.append(
            f"Structured pay from the job board: {rng} {s.currency or '(no currency)'} per {s.period or '?'}"
        )
    if d.normalized.get("conversion"):
        evidence.append(str(d.normalized["conversion"]))
    missing = []
    if d.code in ("SAL_UNDISCLOSED", "SAL_UNDISCLOSED_REVIEW", "STIPEND_UNDISCLOSED", "STIPEND_UNDISCLOSED_REVIEW"):
        missing.append("salary" if d.rule == "SALARY" else "stipend")
    elif d.code.endswith("CURRENCY_UNKNOWN"):
        missing.append("salary currency")
    elif d.code == "SAL_MIN_UNKNOWN":
        missing.append("minimum salary")
    elif d.code.endswith("PERIOD_UNSUPPORTED"):
        missing.append("salary period")
    return Check(d.rule, outcome, d.code, d.reason, evidence, missing)


_MANDATORY = re.compile(
    r"\b(must[- ]have|must know|must be (?:proficient|experienced|skilled)|mandatory|non[- ]negotiable|"
    r"(?:is|are) (?:required|mandatory)|required skills?:|essential)\b",
    re.I,
)


def _segments(text: str) -> list[str]:
    return [p.strip() for line in text.splitlines() for p in re.split(r"(?<=[.;])\s+", line) if p.strip()]


def _mandatory_skills(text: str, required: list[str]) -> list[str]:
    """Required skills named in a sentence that explicitly calls them mandatory."""
    segs = [s for s in _segments(text) if _MANDATORY.search(s)]
    return [r for r in required if any(re.search(rf"(?<!\w){re.escape(r)}(?!\w)", s, re.I) for s in segs)]


def _mandatory_quote(text: str, skill: str) -> str | None:
    for seg in _segments(text):
        if _MANDATORY.search(seg) and re.search(rf"(?<!\w){re.escape(skill)}(?!\w)", seg, re.I):
            return f"“{seg[:180]}”"
    return None


def _skills(job: JobFacts, matched: list[str], related: list[dict], missing_req: list[str]) -> Check:
    req = job.required_skills
    if not req:
        listed = job.preferred_skills
        return Check(
            "SKILLS",
            "unknown",
            "SKILLS_NOT_STATED",
            "The listing doesn't name required skills" + (" (only nice-to-haves)." if listed else "."),
            missing=["required skills"],
        )
    have_req = [s for s in matched if s in req]
    coverage = (len(have_req) + 0.5 * len(related)) / len(req)
    evidence = list(dict.fromkeys(q for s in req[:4] if (q := quote(job.description, re.escape(s)))))[:2]
    detail = f"You have {len(have_req)} of {len(req)} required skills" + (
        f" ({', '.join(have_req[:5])})" if have_req else ""
    )
    if related:
        detail += f", plus {len(related)} closely related"
    if missing_req:
        detail += f"; missing {', '.join(missing_req[:5])}"
    # Rejection needs explicit evidence: skills the listing itself calls mandatory ("must have", …), of
    # which the user has none, not even a related one. Extracted skill lists are noisy (company boilerplate
    # often adds "Security", "Git", …), so zero overlap with the extracted list is only a review.
    related_req = {r["required"] for r in related}
    mandatory = _mandatory_skills(job.description, req)
    lacking = [s for s in mandatory if s not in have_req and s not in related_req]
    if len(mandatory) >= 2 and len(lacking) == len(mandatory):
        quotes = list(dict.fromkeys(q for s in mandatory if (q := _mandatory_quote(job.description, s))))[:2]
        return Check(
            "SKILLS",
            "reject",
            "SKILLS_MANDATORY_MISSING",
            f"The listing says {', '.join(mandatory)} are mandatory; you have none of them.",
            quotes,
        )
    if coverage == 0:
        return Check(
            "SKILLS",
            "review",
            "SKILLS_NONE_MATCHED",
            detail + ". Skill extraction can over-read listings, so check this yourself.",
            evidence,
        )
    if lacking:
        return Check(
            "SKILLS",
            "review",
            "SKILLS_MANDATORY_PARTLY_MISSING",
            detail + f". The listing calls {', '.join(lacking)} mandatory.",
            evidence,
        )
    if coverage < 0.5:
        return Check("SKILLS", "review", "SKILLS_LOW_MATCH", detail + ".", evidence)
    return Check("SKILLS", "pass", "SKILLS_MATCH", detail + ".", evidence)


_YEARS = r"\d{1,2}\s*(?:\+|plus)?\s*(?:-|–|to)?\s*\d{0,2}\s*\+?\s*(?:years|yrs)"


def _stated_minimums(text: str) -> list[float]:
    found = [float(m.group(1)) for m in jp._EXP_RANGE.finditer(text)]
    found += [float(x) for x in jp._EXP_MIN.findall(text)]
    return sorted({v for v in found if v <= 30})


def _experience(job: JobFacts, cand: CandidateProfile) -> Check:
    if job.experience_min is None:
        return Check(
            "EXPERIENCE",
            "unknown",
            "EXPERIENCE_NOT_STATED",
            "The listing doesn't state an experience requirement.",
            missing=["experience requirement"],
        )
    req = (
        f"{job.experience_min:g}–{job.experience_max:g} years"
        if job.experience_max is not None
        else (f"{job.experience_min:g}+ years")
    )
    evidence = [q] if (q := quote(job.description, _YEARS)) else [f"Requires {req}"]
    if cand.years_experience is None:
        return Check(
            "EXPERIENCE",
            "unknown",
            "CANDIDATE_EXPERIENCE_UNKNOWN",
            f"The listing asks for {req}, but your years of experience aren't set.",
            evidence,
            ["your years of experience"],
        )
    mins = _stated_minimums(job.description)
    if len(mins) > 1 and mins[-1] - mins[0] >= 3:
        return Check(
            "EXPERIENCE",
            "review",
            "EXPERIENCE_CONFLICT",
            f"The listing states different experience requirements ({', '.join(f'{m:g}' for m in mins)} years).",
            evidence,
        )
    y = cand.years_experience
    gap = job.experience_min - y
    if gap > 2:
        return Check(
            "EXPERIENCE",
            "reject",
            "EXPERIENCE_SHORT",
            f"Asks for {req}; you have {y:g} ({gap:g} years short).",
            evidence,
        )
    if gap > 1:
        return Check(
            "EXPERIENCE",
            "review",
            "EXPERIENCE_SLIGHTLY_SHORT",
            f"Asks for {req}; you have {y:g} ({gap:g} years short).",
            evidence,
        )
    if job.experience_max is not None and y > job.experience_max + 4:
        return Check(
            "EXPERIENCE",
            "pass",
            "EXPERIENCE_ABOVE_RANGE",
            f"Asks for {req}; you have {y:g} (above the range).",
            evidence,
        )
    return Check("EXPERIENCE", "pass", "EXPERIENCE_MEETS", f"Asks for {req}; you have {y:g}.", evidence)


def _seniority(job: JobFacts, cand: CandidateProfile) -> Check:
    level = job.seniority if job.seniority in SENIORITY_YEARS else None
    title_level = jp.seniority(job.title, None)  # from the title alone
    evidence = [f"Title: “{job.title}”"]
    if level is None:
        return Check(
            "SENIORITY",
            "unknown",
            "SENIORITY_NOT_STATED",
            "The listing doesn't indicate seniority.",
            evidence,
            ["seniority"],
        )
    if title_level and job.experience_min is not None:
        implied = SENIORITY_YEARS[title_level]
        if implied - job.experience_min >= 3:
            return Check(
                "SENIORITY",
                "review",
                "SENIORITY_CONFLICT",
                f"The title says {title_level} (usually {implied:g}+ years) but the listing asks for "
                f"{job.experience_min:g}+ years.",
                evidence,
            )
    if cand.years_experience is None:
        return Check(
            "SENIORITY",
            "unknown",
            "CANDIDATE_EXPERIENCE_UNKNOWN",
            f"{level.title()} role; your years of experience aren't set.",
            evidence,
            ["your years of experience"],
        )
    implied = SENIORITY_YEARS[level] if job.experience_min is None else min(SENIORITY_YEARS[level], job.experience_min)
    gap = implied - cand.years_experience
    if gap > 3:
        return Check(
            "SENIORITY",
            "reject",
            "SENIORITY_TOO_HIGH",
            f"{level.title()} role (typically {implied:g}+ years); you have {cand.years_experience:g}.",
            evidence,
        )
    if gap >= 2:
        return Check(
            "SENIORITY",
            "review",
            "SENIORITY_STRETCH",
            f"{level.title()} role (typically {implied:g}+ years); you have {cand.years_experience:g}.",
            evidence,
        )
    return Check("SENIORITY", "pass", "SENIORITY_FITS", f"{level.title()} role fits your experience.", evidence)


ALL_MODES = frozenset({"remote", "hybrid", "onsite"})
ALL_EMPLOYMENT = frozenset({"full_time", "part_time", "contract", "internship"})
# Location strings that name a country or region but no city ("India", "Remote - APAC").
_REGION_WORDS = frozenset(
    {
        "india",
        "in",
        "apac",
        "emea",
        "asia",
        "europe",
        "uk",
        "usa",
        "us",
        "united",
        "states",
        "kingdom",
        "global",
        "worldwide",
        "anywhere",
        "remote",
        "multiple",
        "locations",
        "various",
        "hybrid",
        "onsite",
        "on",
        "site",
        "office",
    }
)


def _names_no_city(loc: str) -> bool:
    words = re.findall(r"[a-z]+", loc.lower())
    return bool(words) and all(w in _REGION_WORDS for w in words)


def _location(job: JobFacts, cand: CandidateProfile) -> Check:
    """Work arrangement and city. City names are compared via canonical aliases (Bangalore = Bengaluru).
    When the user has a preference and the listing doesn't state the matching fact, it's a review, not a pass."""
    modes = list(cand.work_modes)
    cities = [c for c in cand.preferred_locations if c.strip()]
    mode_matters = bool(modes) and not set(modes) >= ALL_MODES
    loc = (job.location or "").strip()
    evidence = [f"Location: “{loc}”"] if loc else []
    if job.work_mode and (q := quote(job.description, r"\b(remote|hybrid|on-?site|in[- ]office)\b")):
        evidence.append(q)

    def check(outcome: Outcome, code: str, text: str, missing: list[str] | None = None) -> Check:
        return Check("LOCATION", outcome, code, text, evidence, missing or [])

    says_remote = bool(re.search(r"\bremote\b", loc, re.I))
    if job.work_mode and loc and says_remote != (job.work_mode == "remote"):
        return check(
            "review", "LOCATION_CONFLICT", f"The location says “{loc}” but the listing describes {job.work_mode} work."
        )
    if not mode_matters and not cities:
        return check("pass", "LOCATION_NO_PREFERENCE", "You haven't restricted location or work arrangement.")
    if job.work_mode is None and not loc:
        return check(
            "review",
            "LOCATION_NOT_STATED",
            "The listing doesn't state a location or work arrangement, and you have "
            "preferences for both — check before applying.",
            ["location", "work arrangement"],
        )
    if job.work_mode == "remote":
        if modes and "remote" not in modes:
            return check("reject", "LOCATION_MODE_MISMATCH", f"Remote role; you chose {', '.join(modes)} only.")
        return check("pass", "LOCATION_REMOTE_OK", "Remote role, which you accept.")
    if job.work_mode and modes and job.work_mode not in modes:
        return check("reject", "LOCATION_MODE_MISMATCH", f"{job.work_mode.title()} role; you chose {', '.join(modes)}.")
    mode_missing = ["work arrangement"] if job.work_mode is None and mode_matters else []
    if cities:
        if not loc or _names_no_city(loc):
            return check(
                "review",
                "LOCATION_CITY_NOT_STATED",
                "The listing doesn't say which city" + (f" (only “{loc}”)." if loc else "."),
                [*mode_missing, "city"],
            )
        if not any(mentions_city(loc, c) for c in cities):
            return check(
                "reject",
                "LOCATION_CITY_MISMATCH",
                f"{loc} isn't among your preferred locations ({', '.join(cities)}).",
            )
        if mode_missing:
            return check(
                "review",
                "LOCATION_MODE_NOT_STATED",
                f"{loc} is one of your preferred locations, but the work arrangement isn't stated.",
                mode_missing,
            )
        return check("pass", "LOCATION_CITY_MATCH", f"{loc} is one of your preferred locations.")
    if mode_missing:
        return check(
            "review", "LOCATION_MODE_NOT_STATED", "The listing doesn't state the work arrangement.", mode_missing
        )
    return check("pass", "LOCATION_MODE_MATCH", f"{(job.work_mode or 'This').title()} work matches your preference.")


def _employment(job: JobFacts, cand: CandidateProfile) -> Check:
    et = "internship" if job.is_internship else job.employment_type
    restricted = bool(cand.employment_types) and not set(cand.employment_types) >= ALL_EMPLOYMENT
    if et is None:
        if restricted:
            return Check(
                "EMPLOYMENT",
                "review",
                "EMPLOYMENT_NOT_STATED",
                "The listing doesn't state the employment type, and you only want "
                f"{', '.join(t.replace('_', '-') for t in cand.employment_types)} — check before applying.",
                missing=["employment type"],
            )
        return Check(
            "EMPLOYMENT",
            "unknown",
            "EMPLOYMENT_NOT_STATED",
            "The listing doesn't state the employment type.",
            missing=["employment type"],
        )
    label = et.replace("_", "-")
    if not restricted:
        return Check("EMPLOYMENT", "pass", "EMPLOYMENT_NO_PREFERENCE", f"{label.title()}; you haven't restricted this.")
    if et in cand.employment_types:
        return Check("EMPLOYMENT", "pass", "EMPLOYMENT_MATCH", f"{label.title()} matches your preference.")
    return Check(
        "EMPLOYMENT", "reject", "EMPLOYMENT_MISMATCH", f"{label.title()} isn't an employment type you selected."
    )


# ── Verdict ────────────────────────────────────────────────────────────────────────────────────
@dataclass
class EligibilityResult:
    verdict: Verdict
    summary: str
    checks: list[Check]
    reason_codes: list[str]
    decided_by: list[str]
    missing_information: list[str]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["status"] = LEGACY_STATUS[self.verdict]
        d["rules"] = {r: RULES[r] for r in dict.fromkeys(c.rule for c in self.checks)}
        return d


def evaluate_job(
    job: JobFacts,
    cand: CandidateProfile,
    policy: SalaryPolicy,
    *,
    matched: list[str],
    related: list[dict],
    missing_required: list[str],
    fxc: fx.FxContext | None = None,
) -> EligibilityResult:
    checks = [
        _salary(job, policy, fxc),
        _skills(job, matched, related, missing_required),
        _experience(job, cand),
        _seniority(job, cand),
        _location(job, cand),
        _employment(job, cand),
    ]
    rejects = [c for c in checks if c.outcome == "reject"]
    reviews = [c for c in checks if c.outcome == "review"]
    unknown = [c for c in checks if c.outcome == "unknown"]
    missing = list(dict.fromkeys(m for c in checks for m in c.missing))
    if rejects:
        verdict: Verdict = "REJECTED"
        deciding = rejects
        summary = "Rejected: " + " ".join(c.explanation for c in rejects)
    elif reviews:
        verdict, deciding = "MANUAL_REVIEW", reviews
        summary = "Needs your review: " + " ".join(c.explanation for c in reviews)
    else:
        verdict, deciding = "ELIGIBLE", []
        summary = "Eligible on every rule that could be checked."
        if unknown:
            summary += f" Not checked (information missing): {', '.join(c.rule.lower() for c in unknown)}."
    return EligibilityResult(
        verdict=verdict,
        summary=summary,
        checks=checks,
        reason_codes=[c.code for c in checks],
        decided_by=list(dict.fromkeys(c.rule for c in deciding)),
        missing_information=missing,
    )
