"""Salary eligibility rules (PRD §11, Module 6).

Full-time (CTC, INR lakhs per annum) and internship (monthly stipend, INR) rules are evaluated
separately. Default full-time policy:

* undisclosed salary              → eligible
* verified disclosed minimum ≥ threshold → eligible
* verified disclosed maximum < threshold → ineligible
* advertised but unverified pay   → manual review
* non-INR pay is converted only when an explicit rate is configured; otherwise manual review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Decision = Literal["eligible", "ineligible", "manual_review"]

# Conservative static conversion used only for *display hints*; decisions on foreign currency
# go to manual review rather than relying on a possibly stale rate.
LAKH = 100_000


@dataclass(frozen=True)
class SalaryPolicy:
    min_ctc_lpa: float = 8.0
    allow_undisclosed_salary: bool = True
    min_stipend_monthly_inr: float | None = None
    allow_undisclosed_stipend: bool = True


@dataclass(frozen=True)
class SalaryFacts:
    is_internship: bool
    disclosure: str  # undisclosed | advertised | verified
    minimum: float | None
    maximum: float | None
    currency: str | None
    period: str | None  # year | month | hour


@dataclass(frozen=True)
class SalaryDecision:
    decision: Decision
    reason: str


def _to_inr_annual(amount: float, period: str | None) -> float | None:
    if period in (None, "year"):
        return amount
    if period == "month":
        return amount * 12
    return None  # hourly pay can't be annualised reliably


def evaluate(facts: SalaryFacts, policy: SalaryPolicy) -> SalaryDecision:
    if facts.is_internship:
        return _evaluate_stipend(facts, policy)

    if facts.disclosure == "undisclosed" or (facts.minimum is None and facts.maximum is None):
        if policy.allow_undisclosed_salary:
            return SalaryDecision("eligible", "Salary not disclosed — eligible under your policy.")
        return SalaryDecision("manual_review", "Salary not disclosed and your policy requires disclosure.")

    if facts.currency and facts.currency != "INR":
        return SalaryDecision(
            "manual_review",
            f"Pay is listed in {facts.currency}; compare it to your ₹{policy.min_ctc_lpa:g} LPA minimum.",
        )

    if facts.disclosure == "advertised":
        return SalaryDecision("manual_review", "Pay is advertised but not verified — review before applying.")

    lo = _to_inr_annual(facts.minimum, facts.period) if facts.minimum is not None else None
    hi = _to_inr_annual(facts.maximum, facts.period) if facts.maximum is not None else None
    threshold = policy.min_ctc_lpa * LAKH
    if lo is not None and lo >= threshold:
        return SalaryDecision(
            "eligible", f"Verified minimum ₹{lo / LAKH:.1f} LPA meets your ₹{policy.min_ctc_lpa:g} LPA minimum."
        )
    if hi is not None and hi < threshold:
        return SalaryDecision(
            "ineligible", f"Maximum ₹{hi / LAKH:.1f} LPA is below your ₹{policy.min_ctc_lpa:g} LPA minimum."
        )
    return SalaryDecision("manual_review", "The salary range straddles your minimum — review before applying.")


def _evaluate_stipend(facts: SalaryFacts, policy: SalaryPolicy) -> SalaryDecision:
    if facts.disclosure == "undisclosed" or (facts.minimum is None and facts.maximum is None):
        if policy.allow_undisclosed_stipend:
            return SalaryDecision("eligible", "Stipend not disclosed — eligible under your internship policy.")
        return SalaryDecision("manual_review", "Stipend not disclosed and your internship policy requires disclosure.")
    if policy.min_stipend_monthly_inr is None:
        return SalaryDecision("eligible", "No minimum stipend configured.")
    if facts.currency and facts.currency != "INR":
        return SalaryDecision("manual_review", f"Stipend is listed in {facts.currency}.")
    if facts.disclosure == "advertised":
        return SalaryDecision("manual_review", "Stipend is advertised but not verified.")
    monthly = facts.minimum if facts.minimum is not None else facts.maximum
    assert monthly is not None
    if facts.period == "year":
        monthly = monthly / 12
    if monthly >= policy.min_stipend_monthly_inr:
        return SalaryDecision("eligible", f"Stipend ₹{monthly:,.0f}/month meets your minimum.")
    return SalaryDecision(
        "ineligible", f"Stipend ₹{monthly:,.0f}/month is below your ₹{policy.min_stipend_monthly_inr:,.0f} minimum."
    )
