"""Deterministic, explainable job matching (PRD FR-018 – FR-022).

The score is a weighted average of documented factors. A factor whose inputs are unknown
(e.g. the job states no experience requirement) is *excluded* from the denominator and
reported as unknown — missing information never silently counts as a match or a miss.

Because a score built from few known factors is less trustworthy, the final score is shrunk toward
a neutral 50 in proportion to the unknown weight:

    score = known_score × confidence + 50 × (1 − confidence),   confidence = known weight ÷ 100

A listing that names no skills or experience therefore can't outrank a well-specified strong match.
Same inputs + same SCORING_VERSION ⇒ same score.

| factor      | weight | how it is scored                                                    |
|-------------|--------|---------------------------------------------------------------------|
| skills      | 45     | required skills: exact = 1, related = 0.5; preferred add up to +15% |
| experience  | 20     | within range = 1; each year short −0.3; >4y over max = 0.7          |
| role        | 15     | best token overlap between job title and target/past titles         |
| location    | 10     | work-mode and city preference                                       |
| level       | 5      | job seniority vs. chosen experience levels                          |
| employment  | 5      | internship / full-time / contract preference                        |
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from app.domain import fx, taxonomy
from app.domain.eligibility import evaluate_job
from app.domain.job_parsing import normalize_title
from app.domain.locations import mentions_city
from app.domain.salary_policy import SalaryFacts, SalaryPolicy

SCORING_VERSION = "2026.11.3"  # .3: explainable eligibility verdicts, strict salary threshold
NEUTRAL = 50.0
WEIGHTS = {"skills": 45, "experience": 20, "role": 15, "location": 10, "level": 5, "employment": 5}

LEVEL_RANGES: dict[str, tuple[float, float]] = {
    "fresher": (0, 0.5),
    "internship": (0, 1),
    "0-1": (0, 1),
    "1-2": (1, 2),
    "2-3": (2, 3),
    "3-5": (3, 5),
    "5+": (5, 40),
}
_STOP = {"and", "of", "the", "a", "an", "for", "to", "in", "i", "ii", "iii", "iv", "with", "-", "&", "remote"}


@dataclass
class CandidateProfile:
    skills: dict[str, str]  # canonical skill → evidence source ("verified", "resume", "project")
    years_experience: float | None
    target_titles: list[str]
    past_titles: list[str]
    preferred_locations: list[str]
    work_modes: list[str]
    experience_levels: list[str]
    employment_types: list[str]  # full_time | internship | contract | part_time

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass
class JobFacts:
    title: str
    required_skills: list[str]
    preferred_skills: list[str]
    experience_min: float | None
    experience_max: float | None
    location: str | None
    work_mode: str | None
    seniority: str | None
    employment_type: str | None
    is_internship: bool
    salary: SalaryFacts
    description: str = ""  # plain-text listing, used to quote evidence


@dataclass
class Factor:
    key: str
    weight: int
    score: float | None  # 0..1, None = unknown
    summary: str


@dataclass
class MatchResult:
    score: int
    factors: list[Factor]
    matched_skills: list[str]
    related_skills: list[dict[str, str]]
    missing_required: list[str]
    missing_preferred: list[str]
    unknown_factors: list[str]
    strengths: list[str]
    concerns: list[str]
    eligibility: dict = field(default_factory=dict)
    scoring_version: str = SCORING_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def _tokens(title: str) -> set[str]:
    return {t for t in normalize_title(title).split() if t not in _STOP and len(t) > 1}


def _skills_factor(job: JobFacts, cand: CandidateProfile) -> tuple[Factor, list[str], list[dict], list[str], list[str]]:
    have = set(cand.skills)
    matched, related, missing_req, missing_pref = [], [], [], []
    if not job.required_skills and not job.preferred_skills:
        return Factor("skills", WEIGHTS["skills"], None, "The listing doesn't name specific skills."), [], [], [], []

    req_points = 0.0
    for s in job.required_skills:
        if s in have:
            matched.append(s)
            req_points += 1
        else:
            near = next((h for h in have if taxonomy.related(s, h)), None)
            if near:
                related.append({"required": s, "have": near})
                req_points += 0.5
            else:
                missing_req.append(s)
    pref_hits = 0
    for s in job.preferred_skills:
        if s in have:
            matched.append(s)
            pref_hits += 1
        else:
            missing_pref.append(s)

    if job.required_skills:
        base = req_points / len(job.required_skills)
        bonus = 0.15 * (pref_hits / len(job.preferred_skills)) if job.preferred_skills else 0
        score = min(1.0, base + bonus)
    else:
        score = pref_hits / len(job.preferred_skills)
    summary = f"{len(matched)} of {len(job.required_skills) + len(job.preferred_skills)} listed skills matched"
    if related:
        summary += f", {len(related)} adjacent"
    return Factor("skills", WEIGHTS["skills"], score, summary + "."), matched, related, missing_req, missing_pref


def _experience_factor(job: JobFacts, cand: CandidateProfile) -> Factor:
    w = WEIGHTS["experience"]
    if job.experience_min is None:
        return Factor("experience", w, None, "The listing doesn't state an experience requirement.")
    if cand.years_experience is None:
        return Factor("experience", w, None, "Add your years of experience to score this factor.")
    y, lo, hi = cand.years_experience, job.experience_min, job.experience_max
    req = f"{lo:g}–{hi:g} years" if hi is not None else f"{lo:g}+ years"
    if y < lo:
        gap = lo - y
        return Factor("experience", w, max(0.0, 1 - 0.3 * gap), f"Asks for {req}; you have {y:g} ({gap:g} short).")
    if hi is not None and y > hi + 4:
        return Factor("experience", w, 0.7, f"Asks for {req}; you have {y:g} — you may be overqualified.")
    return Factor("experience", w, 1.0, f"Asks for {req}; you have {y:g}.")


def _role_factor(job: JobFacts, cand: CandidateProfile) -> Factor:
    w = WEIGHTS["role"]
    titles = cand.target_titles or cand.past_titles
    if not titles:
        return Factor("role", w, None, "Add target job titles to score role fit.")
    jt = _tokens(job.title)
    best, best_title = 0.0, ""
    for t in titles:
        tt = _tokens(t)
        if not tt or not jt:
            continue
        overlap = len(jt & tt) / len(tt)  # how much of the user's target title the job covers
        if overlap > best:
            best, best_title = overlap, t
    if best == 0:
        return Factor("role", w, 0.0, "The title doesn't resemble your target roles.")
    return Factor("role", w, best, f"Closest to your target “{best_title}”.")


def _location_factor(job: JobFacts, cand: CandidateProfile) -> Factor:
    w = WEIGHTS["location"]
    if not cand.work_modes and not cand.preferred_locations:
        return Factor("location", w, None, "Set location preferences to score this factor.")
    if job.work_mode is None and not job.location:
        return Factor("location", w, None, "The listing doesn't state a location or work mode.")
    if job.work_mode == "remote" and (not cand.work_modes or "remote" in cand.work_modes):
        return Factor("location", w, 1.0, "Remote role, matching your preference.")
    city_hit = any(mentions_city(job.location, p) for p in cand.preferred_locations if p.strip())
    mode_ok = job.work_mode is None or not cand.work_modes or job.work_mode in cand.work_modes
    if city_hit and mode_ok:
        return Factor("location", w, 1.0, f"{job.location} is one of your preferred locations.")
    if city_hit:
        return Factor("location", w, 0.6, f"Preferred city, but {job.work_mode} isn't a work mode you selected.")
    if mode_ok and not cand.preferred_locations:
        return Factor("location", w, 0.8, "Work mode matches; no city preference set.")
    return Factor("location", w, 0.2, f"{job.location or job.work_mode} isn't among your preferred locations.")


def _level_factor(job: JobFacts, cand: CandidateProfile) -> Factor:
    w = WEIGHTS["level"]
    if not cand.experience_levels:
        return Factor("level", w, None, "Choose experience levels to score this factor.")
    if job.experience_min is None and job.seniority is None:
        return Factor("level", w, None, "The listing doesn't indicate seniority.")
    ranges = [LEVEL_RANGES[lv] for lv in cand.experience_levels if lv in LEVEL_RANGES]
    if job.is_internship:
        ok = "internship" in cand.experience_levels
        return Factor(
            "level", w, 1.0 if ok else 0.3, "Internship " + ("matches" if ok else "isn't among") + " your levels."
        )
    if job.experience_min is not None:
        lo = job.experience_min
        hi = job.experience_max if job.experience_max is not None else lo + 2
        ok = any(r_lo <= hi and lo <= r_hi for r_lo, r_hi in ranges)
        return Factor(
            "level", w, 1.0 if ok else 0.2, "Within your chosen levels." if ok else "Outside your chosen levels."
        )
    senior_map = {"intern": (0, 1), "junior": (0, 2), "mid": (2, 5), "senior": (5, 40), "staff+": (8, 40)}
    lo, hi = senior_map.get(job.seniority or "", (0, 40))
    ok = any(r_lo <= hi and lo <= r_hi for r_lo, r_hi in ranges)
    return Factor(
        "level",
        w,
        1.0 if ok else 0.3,
        f"{(job.seniority or '').title()} role " + ("fits" if ok else "may not fit") + " your levels.",
    )


def _employment_factor(job: JobFacts, cand: CandidateProfile) -> Factor:
    w = WEIGHTS["employment"]
    if not cand.employment_types:
        return Factor("employment", w, None, "Choose employment types to score this factor.")
    et = "internship" if job.is_internship else job.employment_type
    if et is None:
        return Factor("employment", w, None, "Employment type not stated.")
    ok = et in cand.employment_types
    return Factor(
        "employment",
        w,
        1.0 if ok else 0.0,
        f"{et.replace('_', '-')} " + ("matches" if ok else "isn't") + " your preference.",
    )


def compute_match(
    job: JobFacts, cand: CandidateProfile, policy: SalaryPolicy, fxc: fx.FxContext | None = None
) -> MatchResult:
    skills_f, matched, related, missing_req, missing_pref = _skills_factor(job, cand)
    factors = [
        skills_f,
        _experience_factor(job, cand),
        _role_factor(job, cand),
        _location_factor(job, cand),
        _level_factor(job, cand),
        _employment_factor(job, cand),
    ]
    known = [f for f in factors if f.score is not None]
    total_w = sum(f.weight for f in known)
    # Confidence: how much of the scoring weight was actually backed by known inputs.
    confidence = round(total_w / sum(WEIGHTS.values()), 2)
    known_score = 100 * sum(f.weight * f.score for f in known) / total_w if total_w else NEUTRAL  # type: ignore[operator]
    score = round(known_score * confidence + NEUTRAL * (1 - confidence))

    strengths = [f.summary for f in known if f.score is not None and f.score >= 0.8]
    concerns = [f.summary for f in known if f.score is not None and f.score < 0.5]
    if missing_req:
        concerns.append("Missing required: " + ", ".join(missing_req[:6]))
    unverified = [s for s in matched if cand.skills.get(s) != "verified"]
    if unverified:
        concerns.append("Not yet verified in your profile: " + ", ".join(unverified[:6]))

    verdict = evaluate_job(
        job, cand, policy, matched=matched, related=related, missing_required=missing_req, fxc=fxc
    ).as_dict()
    salary = next(c for c in verdict["checks"] if c["rule"] in ("SALARY", "STIPEND"))
    return MatchResult(
        score=score,
        factors=factors,
        matched_skills=matched,
        related_skills=related,
        missing_required=missing_req,
        missing_preferred=missing_pref,
        unknown_factors=[f.key for f in factors if f.score is None],
        strengths=strengths,
        concerns=concerns,
        eligibility={
            **verdict,
            # Kept for existing consumers (filters, dashboard, automations, MCP).
            "salary": {
                "decision": {"pass": "eligible", "reject": "ineligible", "review": "manual_review"}[salary["outcome"]],
                "reason": salary["explanation"],
            },
            "blocks": [c["explanation"] for c in verdict["checks"] if c["outcome"] == "reject"],
            "confidence": confidence,
            "known_factor_score": round(known_score),
        },
    )


def experience_level_for_years(years: float | None) -> str | None:
    if years is None:
        return None
    for key in ("fresher", "0-1", "1-2", "2-3", "3-5", "5+"):
        lo, hi = LEVEL_RANGES[key]
        if years < hi or key == "5+":
            return key
    return None


def title_keywords(text: str) -> list[str]:
    return [t for t in re.split(r"[,;/]", text) if t.strip()]
