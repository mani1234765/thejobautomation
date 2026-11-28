"""Phase 3 M2: salary extraction, structured-vs-description conflicts, provenance and engine versioning.
Synthetic data only; HTTP mocked; no network."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.domain import job_parsing as jp
from app.domain.eligibility import salary_conflict
from app.domain.matching import compute_match
from app.domain.salary_policy import SalaryFacts, SalaryPolicy
from app.integrations.sources import providers
from app.integrations.sources.base import FetchResult
from tests.helpers import job
from tests.test_eligibility_policy import CAND, check, facts
from tests.test_workflows import _onboard

V = "/api/v1"
CASES = json.loads((Path(__file__).parent / "data" / "salary_labelled.json").read_text())["cases"]


def _got(text):
    s = jp.parse_salary_text(text)
    if s.minimum is None:
        return None
    return {
        "min": round(s.minimum, 2),
        "max": round(s.maximum, 2),
        "currency": None if s.currency == "$" else s.currency,
        "period": s.period,
    }


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_labelled_salary_snippets(case):
    assert _got(case["text"]) == case["expected"]


def test_labelled_dataset_metrics():
    """Precision/recall over the whole labelled set; a regression in any case fails the parametrised test
    above, this one documents the aggregate and its size."""
    tp = fp = fn = 0
    for c in CASES:
        got, want = _got(c["text"]), c["expected"]
        if got and want and got == want:
            tp += 1
        elif got and want:
            fp, fn = fp + 1, fn + 1
        elif got:
            fp += 1
        elif want:
            fn += 1
    assert len(CASES) == 57 and tp == 44
    assert tp / (tp + fp) == 1.0 and tp / (tp + fn) == 1.0


def test_period_is_never_assumed():
    assert jp.parse_salary_text("₹50,000 - ₹60,000 salary").period is None
    assert jp.parse_salary_text("$211.4K - $290.6K").period is None
    assert jp.parse_salary_text("18 - 26 LPA").period == "year"  # LPA means per annum


def test_bare_dollar_is_kept_as_ambiguous_not_usd():
    assert jp.parse_salary_text("$150,000 - $190,000 per year").currency == "$"


def test_find_salaries_lists_every_figure():
    text = "US: US$150,000 - US$190,000 per year. UK: £90,000 - £110,000 per year."
    assert [(s.currency, s.minimum) for s in jp.find_salaries(text)] == [("USD", 150000), ("GBP", 90000)]


# ── Structured pay vs. the description ─────────────────────────────────────────────────────────
def structured(lo, hi, cur="INR", per="year"):
    return SalaryFacts(is_internship=False, disclosure="verified", minimum=lo, maximum=hi, currency=cur, period=per)


@pytest.mark.parametrize(
    ("pay", "text", "conflict"),
    [
        (structured(1_800_000, 2_600_000), "Compensation: ₹18 - 26 LPA", False),  # same
        (structured(1_800_000, 2_600_000), "Compensation: ₹19 - 25 LPA", False),  # overlapping
        (structured(1_800_000, 2_600_000), "CTC: ₹8 - 10 LPA", True),  # clearly lower
        (structured(1_800_000, 2_600_000), "CTC: ₹40 - 50 LPA", True),  # clearly higher
        (structured(1_800_000, 2_000_000), "CTC ₹20.5 LPA", False),  # within 10% tolerance
        (structured(150_000, 150_000, per="month"), "Salary ₹18,00,000 per annum", False),  # month ↔ year
        (structured(150_000, 150_000, per="month"), "Salary ₹9,00,000 per annum", True),
        (structured(150_000, 190_000, "USD"), "UK: £90,000 - £110,000 per year", False),  # other currency
        (structured(150_000, 190_000, "USD"), "L4: US$120,000 per year. L5: US$170,000 per year", False),  # one fits
        (structured(150_000, 190_000, "USD"), "US$80,000 - US$95,000 per year", True),
        (structured(1_800_000, 2_600_000), "Compensation: ₹8 - 10 lakh", False),  # period unknown: not compared
        (structured(900_000, 1_200_000, None), "Salary ₹5,00,000 per annum", False),  # currency unknown
        (structured(1_800_000, 2_600_000), "Competitive pay.", False),
    ],
)
def test_salary_conflict_detection(pay, text, conflict):
    assert (salary_conflict(pay, text) is not None) is conflict


def test_conflict_is_manual_review_with_both_quotes():
    pay = SalaryFacts(False, "verified", 1_800_000, 2_600_000, "INR", "year", text="₹18 - 26 LPA")
    e = compute_match(
        facts(salary=pay, description="Great team. The CTC for this role is ₹8 - 10 LPA. Apply now."),
        CAND,
        SalaryPolicy(),
    ).eligibility
    c = check(e, "SALARY")
    assert e["verdict"] == "MANUAL_REVIEW" and c["code"] == "SAL_CONFLICT"
    assert c["evidence"] == [
        "Employer-provided salary · 1,800,000–2,600,000 · INR · per year",
        "“The CTC for this role is ₹8 - 10 LPA”",
    ]


def test_conflict_overrides_a_rejection_and_an_eligible_result():
    below = SalaryFacts(False, "verified", 600_000, 700_000, "INR", "year")  # would be rejected
    e = compute_match(facts(salary=below, description="CTC: ₹20 - 24 LPA"), CAND, SalaryPolicy()).eligibility
    assert check(e, "SALARY")["code"] == "SAL_CONFLICT"
    above = SalaryFacts(False, "verified", 2_000_000, 2_400_000, "INR", "year")  # would be eligible
    e = compute_match(facts(salary=above, description="CTC: ₹6 - 7 LPA"), CAND, SalaryPolicy()).eligibility
    assert check(e, "SALARY")["code"] == "SAL_CONFLICT"


def test_threshold_rules_are_unchanged_without_a_conflict():
    pay = SalaryFacts(False, "verified", 800_000, 1_200_000, "INR", "year")
    e = compute_match(facts(salary=pay, description="CTC: ₹8 - 12 LPA"), CAND, SalaryPolicy()).eligibility
    assert check(e, "SALARY")["code"] == "SAL_AT_THRESHOLD"  # strict > threshold, as in Phase 2
    e = compute_match(facts(salary=pay), CAND, SalaryPolicy(min_ctc_lpa=7.5)).eligibility
    assert check(e, "SALARY")["code"] == "SAL_ABOVE_THRESHOLD"


# ── Provenance separate from currency certainty ────────────────────────────────────────────────
def test_employer_salary_without_currency_is_labelled_and_reviewed():
    pay = SalaryFacts(False, "verified", 900_000, 1_200_000, None, "year")
    c = check(compute_match(facts(salary=pay), CAND, SalaryPolicy()).eligibility, "SALARY")
    assert c["outcome"] == "review" and c["code"] == "SAL_CURRENCY_UNKNOWN"
    assert c["explanation"].startswith("Employer-provided salary · Currency not stated")
    assert c["evidence"] == ["Employer-provided salary · 900,000–1,200,000 · Currency not stated · per year"]


def test_ambiguous_dollar_is_labelled():
    pay = SalaryFacts(False, "verified", 150_000, 190_000, "$", None, text="$150K – $190K")
    c = check(compute_match(facts(salary=pay), CAND, SalaryPolicy()).eligibility, "SALARY")
    assert c["code"] == "SAL_CURRENCY_UNKNOWN" and "Currency ambiguous (“$”)" in c["explanation"]
    assert c["evidence"][0] == "Employer-provided salary · 150,000–190,000 · Currency ambiguous ($) · Period not stated"


def test_unknown_period_is_reviewed_and_stipend_rules_are_untouched():
    pay = SalaryFacts(False, "verified", 2_500_000, 3_500_000, "INR", None)
    assert check(compute_match(facts(salary=pay), CAND, SalaryPolicy()).eligibility, "SALARY")["code"] == (
        "SAL_PERIOD_UNSUPPORTED"
    )
    stipend = SalaryFacts(True, "verified", 20_000, 20_000, "INR", "month")
    policy = SalaryPolicy(min_stipend_monthly_inr=20_000)
    e = compute_match(facts(is_internship=True, salary=stipend), CAND, policy).eligibility
    assert check(e, "STIPEND")["code"] == "STIPEND_MEETS_MINIMUM"  # inclusive, as before


@respx.mock
def test_lever_missing_interval_is_not_assumed_annual():
    respx.get("https://api.lever.co/v0/postings/example-co").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "x1",
                    "text": "Engineer",
                    "hostedUrl": "https://jobs.lever.co/example-co/x1",
                    "categories": {},
                    "salaryRange": {"currency": "INR", "min": 1_000_000, "max": 1_400_000},
                }
            ],
        )
    )
    j = providers.LeverAdapter().fetch("example-co").jobs[0]
    assert (j.salary_currency, j.salary_period, j.salary_disclosure) == ("INR", None, "verified")


def test_text_salary_is_advertised_with_stated_period_only():
    n = job("1", "Engineer", "Monthly salary ₹1,20,000. Python.")
    assert (n.salary_min, n.salary_period, n.salary_currency, n.salary_disclosure) == (
        120000,
        "month",
        "INR",
        "advertised",
    )
    n = job("2", "Engineer", "Salary: ₹50,000 - ₹60,000. Python.")
    assert n.salary_period is None


# ── Engine version bump: saved decisions kept and marked outdated ─────────────────────────────
def test_engine_update_marks_saved_decisions_outdated_without_recomputing(user_api, monkeypatch):
    from app.domain import matching
    from app.services import jobs as jobs_svc

    class Board:
        provider = "greenhouse"

        def fetch(self, identifier):
            jobs = [
                job("1", "Backend Engineer", "Python. 2-4 years of experience"),
                job("2", "Data Engineer", "SQL. 2-4 years of experience"),
            ]
            return FetchResult("Acme Payments", jobs, raw_count=2)

    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", Board())
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    ids = {i["title"]: i["id"] for i in user_api.get(f"{V}/jobs").json()["items"]}
    saved = ids["Backend Engineer"]
    user_api.put(f"{V}/jobs/{saved}/state", json={"state": "saved"})
    before = user_api.get(f"{V}/jobs/{saved}").json()["eligibility_detail"]
    other_before = user_api.get(f"{V}/jobs/{ids['Data Engineer']}").json()["eligibility_detail"]["evaluated_at"]
    assert before["inputs"]["engine"] == matching.SCORING_VERSION == "2026.11.5"

    monkeypatch.setattr(matching, "SCORING_VERSION", "2099.1.1")
    monkeypatch.setattr(jobs_svc, "SCORING_VERSION", "2099.1.1")
    kept = user_api.get(f"{V}/jobs/{saved}").json()
    assert kept["eligibility_detail"]["evaluated_at"] == before["evaluated_at"]  # not recomputed
    assert kept["eligibility_outdated_reasons"] == ["engine"]
    other = user_api.get(f"{V}/jobs/{ids['Data Engineer']}").json()
    assert other["eligibility_detail"]["evaluated_at"] != other_before and not other["eligibility_outdated"]
    fresh = user_api.post(f"{V}/jobs/{saved}/reevaluate").json()
    assert fresh["eligibility_outdated"] is False and fresh["eligibility_detail"]["inputs"]["engine"] == "2099.1.1"
