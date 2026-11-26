"""Salary eligibility rules (PRD §11, Module 6; policy confirmed 2026-11-27).

Full-time (annual CTC, compared in INR) and internship (monthly stipend, INR) rules are separate.

Full-time policy, with the user's threshold T (default ₹8 LPA, editable in Settings):

| Situation                                                   | Decision      | Code                      |
|-------------------------------------------------------------|---------------|---------------------------|
| Salary not disclosed                                        | eligible      | SAL_UNDISCLOSED           |
|   … and the user turned "allow undisclosed" off             | manual review | SAL_UNDISCLOSED_REVIEW    |
| Pay only advertised in free text (not verified)             | manual review | SAL_UNVERIFIED            |
| Currency missing or ambiguous (e.g. a bare "$")             | manual review | SAL_CURRENCY_UNKNOWN      |
| Pay period missing or hourly                                | manual review | SAL_PERIOD_UNSUPPORTED    |
| Foreign currency without a fresh exchange rate              | manual review | SAL_FX_UNAVAILABLE        |
| Verified minimum > T (strictly)                             | eligible      | SAL_ABOVE_THRESHOLD       |
| Verified minimum = T                                        | rejected      | SAL_AT_THRESHOLD          |
| Verified minimum < T (including ranges like 6–12 when T=8)  | rejected      | SAL_BELOW_THRESHOLD       |
| Only a maximum is verified and it is ≤ T                    | rejected      | SAL_MAX_NOT_ABOVE         |
| Only a maximum is verified and it is > T                    | manual review | SAL_MIN_UNKNOWN           |

Amounts are normalised to annual INR before comparing (monthly × 12; foreign currency via fx.py).
INR never gets assumed: a verified figure without an identifiable currency always goes to review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.domain import fx

Decision = Literal["eligible", "ineligible", "manual_review"]

LAKH = 100_000
DEFAULT_MIN_CTC_LPA = 8.0
# Upper bound for the user's threshold: ₹200 LPA (₹2 crore a year). Above this a typo is far more
# likely than a real requirement, and an unbounded value would silently reject every job.
MAX_MIN_CTC_LPA = 200.0
MAX_DAILY_APPLICATIONS = 10_000


@dataclass(frozen=True)
class SalaryPolicy:
    min_ctc_lpa: float = DEFAULT_MIN_CTC_LPA
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
    text: str | None = None  # the listing's own wording, when there is any


@dataclass(frozen=True)
class SalaryDecision:
    decision: Decision
    reason: str
    code: str = ""
    rule: str = "SALARY"
    normalized: dict = field(default_factory=dict)  # {min_inr_annual, max_inr_annual, currency, conversion}


def _fmt_lpa(amount_inr: float) -> str:
    return f"₹{amount_inr / LAKH:,.2f} LPA".replace(".00 LPA", " LPA")


def _annual(amount: float | None, period: str | None) -> float | None:
    if amount is None:
        return None
    return amount * 12 if period == "month" else amount


def evaluate(facts: SalaryFacts, policy: SalaryPolicy, fxc: fx.FxContext | None = None) -> SalaryDecision:
    if facts.is_internship:
        return _evaluate_stipend(facts, policy, fxc)
    t_lpa = policy.min_ctc_lpa
    threshold = round(t_lpa * LAKH, 2)
    t_txt = f"₹{t_lpa:g} LPA"

    if facts.disclosure == "undisclosed" or (facts.minimum is None and facts.maximum is None):
        if policy.allow_undisclosed_salary:
            return SalaryDecision("eligible", "Salary not disclosed — eligible under your policy.", "SAL_UNDISCLOSED")
        return SalaryDecision(
            "manual_review", "Salary not disclosed and your policy requires disclosure.", "SAL_UNDISCLOSED_REVIEW"
        )
    if facts.disclosure != "verified":
        return SalaryDecision(
            "manual_review", "Pay is only advertised in the description, not verified — review it.", "SAL_UNVERIFIED"
        )
    currency = fx.identify(facts.currency)
    if currency is None:
        shown = f" (“{facts.currency}”)" if facts.currency else ""
        return SalaryDecision(
            "manual_review",
            f"The pay's currency is missing or ambiguous{shown}; JobPilot won't assume INR.",
            "SAL_CURRENCY_UNKNOWN",
        )
    if facts.period not in ("year", "month"):
        return SalaryDecision(
            "manual_review",
            "The pay period is " + (f"“{facts.period}”" if facts.period else "not stated") + "; it can't be "
            "compared with an annual CTC reliably.",
            "SAL_PERIOD_UNSUPPORTED",
        )

    normalized: dict = {"currency": currency}
    values: dict[str, float | None] = {}
    for key, raw in (("min", facts.minimum), ("max", facts.maximum)):
        annual = _annual(raw, facts.period)
        if annual is None:
            values[key] = None
            continue
        conv = fx.to_inr(annual, currency, fxc)
        if not conv.ok:
            return SalaryDecision(
                "manual_review",
                f"Pay is in {currency}. {conv.note} Review it yourself.",
                "SAL_FX_UNAVAILABLE",
                normalized={**normalized, "conversion": conv.code},
            )
        values[key] = round(conv.amount_inr, 2)  # type: ignore[arg-type]
        if conv.code == "FX_CONVERTED":
            normalized["conversion"] = conv.note
    lo, hi = values["min"], values["max"]
    normalized.update(min_inr_annual=lo, max_inr_annual=hi)

    if lo is not None:
        if lo > threshold:
            return SalaryDecision(
                "eligible",
                f"Verified minimum {_fmt_lpa(lo)} is above your {t_txt} threshold.",
                "SAL_ABOVE_THRESHOLD",
                normalized=normalized,
            )
        if lo == threshold:
            return SalaryDecision(
                "ineligible",
                f"Verified minimum {_fmt_lpa(lo)} equals your {t_txt} threshold; it must be strictly greater.",
                "SAL_AT_THRESHOLD",
                normalized=normalized,
            )
        return SalaryDecision(
            "ineligible",
            f"Verified minimum {_fmt_lpa(lo)} is below your {t_txt} threshold"
            + (f" (range up to {_fmt_lpa(hi)})." if hi is not None and hi != lo else "."),
            "SAL_BELOW_THRESHOLD",
            normalized=normalized,
        )
    assert hi is not None
    if hi <= threshold:
        return SalaryDecision(
            "ineligible",
            f"Verified maximum {_fmt_lpa(hi)} is not above your {t_txt} threshold.",
            "SAL_MAX_NOT_ABOVE",
            normalized=normalized,
        )
    return SalaryDecision(
        "manual_review",
        f"Only a maximum ({_fmt_lpa(hi)}) is verified; the minimum is unknown.",
        "SAL_MIN_UNKNOWN",
        normalized=normalized,
    )


def _evaluate_stipend(facts: SalaryFacts, policy: SalaryPolicy, fxc: fx.FxContext | None) -> SalaryDecision:
    """Internship stipends: monthly INR, compared with the (optional) minimum stipend, inclusive."""
    rule = "STIPEND"
    if facts.disclosure == "undisclosed" or (facts.minimum is None and facts.maximum is None):
        if policy.allow_undisclosed_stipend:
            return SalaryDecision(
                "eligible",
                "Stipend not disclosed — eligible under your internship policy.",
                "STIPEND_UNDISCLOSED",
                rule,
            )
        return SalaryDecision(
            "manual_review",
            "Stipend not disclosed and your internship policy requires disclosure.",
            "STIPEND_UNDISCLOSED_REVIEW",
            rule,
        )
    if policy.min_stipend_monthly_inr is None:
        return SalaryDecision("eligible", "No minimum stipend configured.", "STIPEND_NO_MINIMUM", rule)
    if facts.disclosure != "verified":
        return SalaryDecision("manual_review", "Stipend is advertised but not verified.", "STIPEND_UNVERIFIED", rule)
    currency = fx.identify(facts.currency)
    if currency is None:
        return SalaryDecision(
            "manual_review", "The stipend's currency is missing or ambiguous.", "STIPEND_CURRENCY_UNKNOWN", rule
        )
    if facts.period not in ("year", "month"):
        return SalaryDecision(
            "manual_review", "The stipend period can't be converted to monthly.", "STIPEND_PERIOD_UNSUPPORTED", rule
        )
    amount = facts.minimum if facts.minimum is not None else facts.maximum
    assert amount is not None
    monthly = amount / 12 if facts.period == "year" else amount
    conv = fx.to_inr(monthly, currency, fxc)
    if not conv.ok:
        return SalaryDecision("manual_review", f"Stipend is in {currency}. {conv.note}", "STIPEND_FX_UNAVAILABLE", rule)
    monthly_inr = conv.amount_inr or 0.0
    if monthly_inr >= policy.min_stipend_monthly_inr:
        return SalaryDecision(
            "eligible", f"Stipend ₹{monthly_inr:,.0f}/month meets your minimum.", "STIPEND_MEETS_MINIMUM", rule
        )
    return SalaryDecision(
        "ineligible",
        f"Stipend ₹{monthly_inr:,.0f}/month is below your ₹{policy.min_stipend_monthly_inr:,.0f} minimum.",
        "STIPEND_BELOW_MINIMUM",
        rule,
    )
