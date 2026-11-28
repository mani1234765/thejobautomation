"""Discovery filters, shared by job search and discovery automations so both read a listing the same way.

Filters only decide what is shown or shortlisted. They never change, override or recompute an eligibility
decision. A listing that doesn't state the filtered fact stays included and is shown as "Not stated", so
missing information never hides a job:

| Filter            | Included                                                                          |
|-------------------|-----------------------------------------------------------------------------------|
| employment type   | the listing's type is selected, or the type isn't stated                          |
| work mode         | the arrangement is selected, or it isn't stated                                   |
| experience level  | the stated range overlaps a selected level, or experience isn't stated            |
| minimum pay (LPA) | annual INR pay reaches it, or pay, currency or a yearly/monthly period isn't known |
| posted within     | posted inside the window, or the posting date isn't stated (applied in the query) |

An internship-flagged listing counts as "internship" everywhere, as it does in the eligibility rules.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from app.domain.job_parsing import EMPLOYMENT_TYPES

LAKH = 100_000
WORK_MODES = ("remote", "hybrid", "onsite")
# Experience levels as (lowest, highest) years. "internship" also matches internship-flagged listings.
EXPERIENCE_LEVELS: dict[str, tuple[float, float]] = {
    "fresher": (0, 0),
    "internship": (0, 1),
    "0-1": (0, 1),
    "1-2": (1, 2),
    "2-3": (2, 3),
    "3-5": (3, 5),
    "5+": (5, 99),
}
CHOICES: dict[str, tuple[str, ...]] = {
    "employment_types": EMPLOYMENT_TYPES,
    "work_modes": WORK_MODES,
    "experience_levels": tuple(EXPERIENCE_LEVELS),
}


class Listing(Protocol):
    employment_type: str | None
    is_internship: bool
    work_mode: str | None
    experience_min: float | None
    experience_max: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None


class InvalidFilter(ValueError):
    def __init__(self, name: str, bad: Iterable[str], allowed: Iterable[str]) -> None:
        self.name, self.bad, self.allowed = name, sorted(bad), list(allowed)
        super().__init__(f"Unknown {name}: {', '.join(self.bad)}. Allowed: {', '.join(self.allowed)}.")


def choices(name: str, values: Any) -> list[str]:
    """Validate a multiple-choice filter from a client. A single string counts as one value (older clients);
    duplicates are dropped and order kept. Raises InvalidFilter naming the unknown values."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise InvalidFilter(name, [repr(values)], CHOICES[name])
    bad = {v for v in values if v not in CHOICES[name]}
    if bad:
        raise InvalidFilter(name, bad, CHOICES[name])
    return list(dict.fromkeys(values))


def stored_choices(name: str, values: Any) -> tuple[list[str], list[str]]:
    """Read a multiple-choice filter saved earlier, without failing: (known values, unrecognised values)."""
    if values is None:
        return [], []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return [], [repr(values)]
    known = [v for v in values if isinstance(v, str) and v in CHOICES[name]]
    return list(dict.fromkeys(known)), sorted({str(v) for v in values} - set(known))


def employment_kind(job: Listing) -> str | None:
    return "internship" if job.is_internship else job.employment_type


def employment_ok(job: Listing, types: list[str]) -> bool:
    kind = employment_kind(job)
    return not types or kind is None or kind in types


def work_mode_ok(job: Listing, modes: list[str]) -> bool:
    return not modes or job.work_mode is None or job.work_mode in modes


def experience_ok(job: Listing, levels: list[str]) -> bool:
    if not levels or job.experience_min is None:
        return True
    lo_job = job.experience_min
    hi_job = job.experience_max if job.experience_max is not None else lo_job + 2
    for lv in levels:
        if lv == "internship" and job.is_internship:
            return True
        lo, hi = EXPERIENCE_LEVELS[lv]
        if lo_job <= hi and hi_job >= lo:
            return True
    return False


def annual_inr_max(job: Listing) -> float | None:
    """The top of the listing's pay as annual INR, or None when it can't be known without guessing (no pay,
    a currency other than a stated INR, or a period that isn't yearly or monthly)."""
    if job.salary_max is None or job.salary_currency != "INR" or job.salary_period not in ("year", "month"):
        return None
    return job.salary_max * 12 if job.salary_period == "month" else job.salary_max


def pay_ok(job: Listing, min_lpa: float | None) -> bool:
    if min_lpa is None:
        return True
    annual = annual_inr_max(job)
    return annual is None or annual >= min_lpa * LAKH
