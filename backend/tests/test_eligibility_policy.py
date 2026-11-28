"""Phase 2: salary policy boundaries, currency handling, configurable threshold, daily limit, human-only
approval and explainable eligibility verdicts. Synthetic data only; no network."""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_factory
from app.domain import fx
from app.domain.matching import CandidateProfile, JobFacts, compute_match
from app.domain.salary_policy import MAX_DAILY_APPLICATIONS, SalaryFacts, SalaryPolicy, evaluate
from app.integrations.sources import providers
from app.models import AuditEvent, UserSettings
from app.services import applications as app_svc
from tests.conftest import make_client, register
from tests.helpers import FakeAdapter, job

V = "/api/v1"
TODAY = date(2026, 11, 27)
LAKH = 100_000


def sal(minimum=None, maximum=None, currency="INR", period="year", disclosure="verified", **kw):
    return SalaryFacts(
        is_internship=kw.pop("is_internship", False),
        disclosure=disclosure,
        minimum=minimum,
        maximum=maximum,
        currency=currency,
        period=period,
        **kw,
    )


def fresh(rates=None, as_of=TODAY, max_age=7):
    table = fx.RateTable("Synthetic test rates", as_of, rates or {"USD": 84.0, "PLN": 21.0})
    return fx.FxContext(table, TODAY, max_age)


# ── Salary threshold boundaries (default ₹8 LPA) ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("facts", "decision", "code"),
    [
        (sal(8 * LAKH, 10 * LAKH), "ineligible", "SAL_AT_THRESHOLD"),  # exactly ₹8 LPA
        (sal(8 * LAKH, 8 * LAKH), "ineligible", "SAL_AT_THRESHOLD"),
        (sal(801_000, 900_000), "eligible", "SAL_ABOVE_THRESHOLD"),  # ₹8.01 LPA
        (sal(800_001, None), "eligible", "SAL_ABOVE_THRESHOLD"),  # one rupee above
        (sal(799_999, 2_000_000), "ineligible", "SAL_BELOW_THRESHOLD"),
        (sal(6 * LAKH, 12 * LAKH), "ineligible", "SAL_BELOW_THRESHOLD"),  # ₹6–12 LPA: minimum below
        (sal(None, 8 * LAKH), "ineligible", "SAL_MAX_NOT_ABOVE"),
        (sal(None, 15 * LAKH), "manual_review", "SAL_MIN_UNKNOWN"),
        (sal(disclosure="undisclosed", currency=None, period=None), "eligible", "SAL_UNDISCLOSED"),
        (sal(20 * LAKH, 25 * LAKH, disclosure="advertised"), "manual_review", "SAL_UNVERIFIED"),
        (sal(20 * LAKH, 25 * LAKH, currency=None), "manual_review", "SAL_CURRENCY_UNKNOWN"),  # never assume INR
        (sal(150_000, 190_000, currency="$"), "manual_review", "SAL_CURRENCY_UNKNOWN"),  # ambiguous dollar
        (sal(70_000, 90_000, period="month"), "eligible", "SAL_ABOVE_THRESHOLD"),  # ₹8.4 LPA after ×12
        (sal(66_667, 90_000, period="month"), "eligible", "SAL_ABOVE_THRESHOLD"),  # ₹8,00,004/yr
        (sal(66_666, 90_000, period="month"), "ineligible", "SAL_BELOW_THRESHOLD"),  # ₹7,99,992/yr
        (sal(5_000, 6_000, period="hour"), "manual_review", "SAL_PERIOD_UNSUPPORTED"),
        (sal(9 * LAKH, 10 * LAKH, period=None), "manual_review", "SAL_PERIOD_UNSUPPORTED"),
    ],
)
def test_default_threshold_boundaries(facts, decision, code):
    d = evaluate(facts, SalaryPolicy(), fresh())
    assert (d.decision, d.code) == (decision, code), d.reason


def test_threshold_is_strict_for_custom_values():
    p12 = SalaryPolicy(min_ctc_lpa=12)
    assert evaluate(sal(12 * LAKH, 14 * LAKH), p12).code == "SAL_AT_THRESHOLD"
    assert evaluate(sal(1_201_000, 14 * LAKH), p12).decision == "eligible"
    assert evaluate(sal(10 * LAKH, 20 * LAKH), p12).decision == "ineligible"
    assert evaluate(sal(801_000, None), SalaryPolicy(min_ctc_lpa=8.01)).code == "SAL_AT_THRESHOLD"
    assert evaluate(sal(1, None), SalaryPolicy(min_ctc_lpa=0)).decision == "eligible"


# ── Currency identification and conversion ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("₹", "INR"),
        ("Rs.", "INR"),
        ("inr", "INR"),
        ("US$", "USD"),
        ("usd", "USD"),
        ("zł", "PLN"),
        ("PLN", "PLN"),
        ("€", "EUR"),
        ("C$", "CAD"),
        ("$", None),
        ("", None),
        (None, None),
        ("dollars", None),
    ],
)
def test_currency_identification(raw, code):
    assert fx.identify(raw) == code


def test_usd_and_pln_are_converted_with_a_fresh_documented_rate():
    d = evaluate(sal(10_000, 12_000, currency="USD"), SalaryPolicy(), fresh())  # $10k × 84 = ₹8.4 LPA
    assert d.decision == "eligible"
    assert d.normalized["min_inr_annual"] == 840_000
    assert "84 INR/USD" in d.normalized["conversion"] and "Synthetic test rates" in d.normalized["conversion"]
    assert "2026-11-27" in d.normalized["conversion"]
    d = evaluate(sal(248_000, 372_000, currency="zł"), SalaryPolicy(), fresh())  # zł → PLN
    assert d.decision == "eligible" and d.normalized["currency"] == "PLN"
    # Converted boundary is still strict: $9,523.81 × 84 = ₹8,00,000.04 > ₹8 L, $9,523.80 is not.
    assert evaluate(sal(9_523.81, None, currency="USD"), SalaryPolicy(), fresh()).decision == "eligible"
    assert evaluate(sal(9_523.80, None, currency="USD"), SalaryPolicy(), fresh()).decision == "ineligible"


@pytest.mark.parametrize(
    ("ctx", "why"),
    [
        (None, "No exchange-rate snapshot"),
        (fx.FxContext(None, TODAY, 7), "No exchange-rate snapshot"),
        (fresh(as_of=TODAY - timedelta(days=8)), "8 days old"),
        (fresh(rates={"EUR": 90.0}), "No USD→INR rate"),
    ],
)
def test_conversion_failures_go_to_manual_review(ctx, why):
    d = evaluate(sal(50_000, 60_000, currency="USD"), SalaryPolicy(), ctx)
    assert d.decision == "manual_review" and d.code == "SAL_FX_UNAVAILABLE" and why in d.reason


def test_rate_snapshot_is_validated(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="source"):
        fx.parse_rate_table({"as_of": "2026-11-27", "rates_to_inr": {"USD": 84}})
    with pytest.raises(ValueError, match="positive"):
        fx.parse_rate_table({"source": "x", "as_of": "2026-11-27", "rates_to_inr": {"USD": -1}})
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        fx.parse_rate_table({"source": "x", "as_of": "yesterday", "rates_to_inr": {"USD": 84}})
    bad = tmp_path / "rates.json"
    bad.write_text("{not json")
    assert fx.load_rate_table(str(bad)) is None  # invalid file → no conversion, never a guess
    assert fx.load_rate_table(str(tmp_path / "missing.json")) is None
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"source": "RBI reference rate", "as_of": "2026-11-26", "rates_to_inr": {"USD": 84.1}}))
    table = fx.load_rate_table(str(good))
    assert table and table.rates_to_inr == {"USD": 84.1} and table.as_of == date(2026, 11, 26)


def test_internship_stipend_rules_stay_separate():
    stipend = sal(20_000, 20_000, period="month", is_internship=True)
    assert evaluate(stipend, SalaryPolicy()).code == "STIPEND_NO_MINIMUM"  # never judged against the CTC threshold
    assert evaluate(stipend, SalaryPolicy(min_stipend_monthly_inr=25_000)).code == "STIPEND_BELOW_MINIMUM"
    assert evaluate(stipend, SalaryPolicy(min_stipend_monthly_inr=20_000)).code == "STIPEND_MEETS_MINIMUM"
    no_cur = sal(20_000, 20_000, currency=None, period="month", is_internship=True)
    assert evaluate(no_cur, SalaryPolicy(min_stipend_monthly_inr=10_000)).code == "STIPEND_CURRENCY_UNKNOWN"


# ── Eligibility verdicts with evidence ────────────────────────────────────────────────────────────
CAND = CandidateProfile(
    skills={"Python": "verified", "PostgreSQL": "resume", "AWS": "resume", "Docker": "resume"},
    years_experience=3.0,
    target_titles=["Backend Engineer"],
    past_titles=[],
    preferred_locations=["Bengaluru"],
    work_modes=["hybrid", "remote"],
    experience_levels=["2-3", "3-5"],
    employment_types=["full_time"],
)


def facts(**kw):
    base = dict(
        title="Backend Engineer",
        required_skills=["Python", "PostgreSQL", "AWS"],
        preferred_skills=["Kubernetes"],
        experience_min=2.0,
        experience_max=4.0,
        location="Bengaluru, India",
        work_mode="hybrid",
        seniority="mid",
        employment_type="full_time",
        is_internship=False,
        salary=sal(disclosure="undisclosed", currency=None),
        description="Requirements\n• 2-4 years of experience with Python, PostgreSQL and AWS\nHybrid in Bengaluru.",
    )
    base.update(kw)
    return JobFacts(**base)


def verdict(job, cand=CAND, policy=None):
    return compute_match(job, cand, policy or SalaryPolicy(), fresh()).eligibility


def check(e, rule):
    return next(c for c in e["checks"] if c["rule"] == rule)


def test_eligible_verdict_explains_every_rule_with_evidence():
    e = verdict(facts())
    assert e["verdict"] == "ELIGIBLE" and e["status"] == "eligible" and e["decided_by"] == []
    assert {c["rule"] for c in e["checks"]} == {"SALARY", "SKILLS", "EXPERIENCE", "SENIORITY", "LOCATION", "EMPLOYMENT"}
    exp = check(e, "EXPERIENCE")
    assert exp["code"] == "EXPERIENCE_MEETS" and "2-4 years of experience" in exp["evidence"][0]
    assert "Python" in check(e, "SKILLS")["evidence"][0]
    assert check(e, "LOCATION")["evidence"][0] == "Location: “Bengaluru, India”"
    assert e["missing_information"] == ["salary"]  # undisclosed is eligible, but reported as missing
    assert set(e["rules"]) == {c["rule"] for c in e["checks"]} and all(e["rules"].values())
    assert e["reason_codes"] == [c["code"] for c in e["checks"]]


def test_salary_rejection_names_the_rule_and_quotes_the_listing():
    e = verdict(facts(salary=sal(8 * LAKH, 12 * LAKH, text="CTC: ₹8 - 12 LPA")))
    assert e["verdict"] == "REJECTED" and e["decided_by"] == ["SALARY"]
    s = check(e, "SALARY")
    assert s["code"] == "SAL_AT_THRESHOLD" and s["evidence"] == [
        "Employer-provided salary · 800,000–1,200,000 · INR · per year",  # provenance first (M2)
        "“CTC: ₹8 - 12 LPA”",
    ]
    assert e["blocks"] == [s["explanation"]] and e["salary"]["decision"] == "ineligible"


def test_skills_none_matched_needs_review_and_low_match_needs_review():
    # Zero overlap with *extracted* skills is a review, not a rejection (extraction is noisy until Phase 3).
    e = verdict(facts(required_skills=["Salesforce", "Excel", "Tableau"], description="We use Salesforce daily."))
    assert e["verdict"] == "MANUAL_REVIEW" and check(e, "SKILLS")["code"] == "SKILLS_NONE_MATCHED"
    assert check(e, "SKILLS")["evidence"] == ["“We use Salesforce daily”"]  # quoted once, not per skill
    e = verdict(facts(required_skills=["Python", "Go", "Rust", "Kafka"]))
    assert e["verdict"] == "MANUAL_REVIEW" and check(e, "SKILLS")["code"] == "SKILLS_LOW_MATCH"


def test_experience_rules_and_conflicting_requirements():
    five = "Requirements\n• 5+ years of experience building backend services."
    short = verdict(
        facts(experience_min=5.0, experience_max=None, seniority="senior", description=five),
        CandidateProfile(**{**CAND.__dict__, "years_experience": 2.0}),
    )
    c = check(short, "EXPERIENCE")
    assert short["verdict"] == "REJECTED" and c["code"] == "EXPERIENCE_SHORT"
    assert "5+ years of experience" in c["evidence"][0]
    stretch = verdict(
        facts(experience_min=5.0, experience_max=None, seniority="senior", description=five),
        CandidateProfile(**{**CAND.__dict__, "years_experience": 3.5}),
    )
    assert check(stretch, "EXPERIENCE")["code"] == "EXPERIENCE_SLIGHTLY_SHORT" and stretch["verdict"] == "MANUAL_REVIEW"
    conflict = verdict(
        facts(
            experience_min=2.0,
            description="2+ years of experience required.\nRequirements\n• 7+ years of backend experience",
        )
    )
    assert check(conflict, "EXPERIENCE")["code"] == "EXPERIENCE_CONFLICT" and conflict["verdict"] == "MANUAL_REVIEW"
    # M3: a preferred figure ("ideally 7+") is kept apart from the requirement, so it is not a conflict.
    preferred = verdict(
        facts(
            experience_min=2.0,
            description="2+ years of experience required. Ideally 7+ years of experience in fintech.",
        )
    )
    assert check(preferred, "EXPERIENCE")["code"] == "EXPERIENCE_MEETS"


def test_seniority_from_title_is_evidenced():
    e = verdict(
        facts(
            title="Senior Staff Engineer",
            seniority="staff+",
            experience_min=None,
            experience_max=None,
            description="Lead our platform.",
        ),
        CandidateProfile(**{**CAND.__dict__, "years_experience": 1.0}),
    )
    c = check(e, "SENIORITY")
    assert e["verdict"] == "REJECTED" and c["code"] == "SENIORITY_TOO_HIGH"
    assert c["evidence"] == ["Title: “Senior Staff Engineer”"]
    conflict = verdict(
        facts(
            title="Senior Backend Engineer",
            seniority="senior",
            experience_min=1.0,
            experience_max=2.0,
            description="1-2 years of experience.",
        )
    )
    assert check(conflict, "SENIORITY")["code"] == "SENIORITY_CONFLICT"


def test_location_mismatch_and_contradictory_listing():
    e = verdict(facts(location="Mumbai, India", work_mode="onsite", description="Onsite in Mumbai."))
    c = check(e, "LOCATION")
    assert e["verdict"] == "REJECTED" and c["code"] == "LOCATION_MODE_MISMATCH"
    assert "Location: “Mumbai, India”" in c["evidence"]
    e = verdict(facts(location="Mumbai, India", work_mode="hybrid", description="Hybrid, Mumbai office."))
    assert check(e, "LOCATION")["code"] == "LOCATION_CITY_MISMATCH"
    e = verdict(facts(location="Remote - India", work_mode="onsite", description="This role is onsite in Pune."))
    assert check(e, "LOCATION")["code"] == "LOCATION_CONFLICT" and e["verdict"] == "MANUAL_REVIEW"


NOTHING_STATED = dict(
    title="Engineer",
    required_skills=[],
    preferred_skills=[],
    experience_min=None,
    experience_max=None,
    location=None,
    work_mode=None,
    seniority=None,
    employment_type=None,
    description="Join us.",
)


def test_missing_information_with_user_preferences_needs_review():
    # The user restricts location, work arrangement and employment type; the listing states none of them.
    e = verdict(facts(**NOTHING_STATED))
    assert e["verdict"] == "MANUAL_REVIEW" and set(e["decided_by"]) == {"LOCATION", "EMPLOYMENT"}
    assert check(e, "LOCATION")["code"] == "LOCATION_NOT_STATED"
    assert check(e, "EMPLOYMENT")["code"] == "EMPLOYMENT_NOT_STATED" and check(e, "EMPLOYMENT")["outcome"] == "review"
    unknown = {c["rule"] for c in e["checks"] if c["outcome"] == "unknown"}
    assert unknown == {"SKILLS", "EXPERIENCE", "SENIORITY"}  # nothing is assumed, nothing rejects on absence
    assert {
        "required skills",
        "experience requirement",
        "seniority",
        "location",
        "work arrangement",
        "employment type",
        "salary",
    } <= set(e["missing_information"])


def test_missing_information_without_preferences_is_reported_not_rejected():
    free = CandidateProfile(**{**CAND.__dict__, "preferred_locations": [], "work_modes": [], "employment_types": []})
    e = verdict(facts(**NOTHING_STATED), free)
    assert e["verdict"] == "ELIGIBLE" and "Not checked" in e["summary"]
    assert check(e, "EMPLOYMENT")["outcome"] == "unknown" and check(e, "LOCATION")["code"] == "LOCATION_NO_PREFERENCE"


def test_candidate_data_missing_is_unknown():
    e = verdict(facts(), CandidateProfile(**{**CAND.__dict__, "years_experience": None}))
    assert check(e, "EXPERIENCE")["code"] == "CANDIDATE_EXPERIENCE_UNKNOWN"
    assert "your years of experience" in e["missing_information"]


# ── Settings: configurable threshold and daily limit (API) ──────────────────────────────────────
def _settings(api):
    return api.get(f"{V}/settings").json()


def _put(api, **changes):
    return api.put(f"{V}/settings", json={**_settings(api), **changes})


def test_defaults_are_8_lpa_and_10000_per_day(user_api):
    s = _settings(user_api)
    assert s["min_ctc_lpa"] == 8.0 and s["daily_application_limit"] == MAX_DAILY_APPLICATIONS == 10_000


@pytest.mark.parametrize("value", [1, 10, 9_999, 10_000])
def test_daily_limit_accepts_valid_values(user_api, value):
    r = _put(user_api, daily_application_limit=value)
    assert r.status_code == 200 and r.json()["daily_application_limit"] == value


@pytest.mark.parametrize("value", [10_001, 0, -5, 10**9, "ten"])
def test_daily_limit_rejects_out_of_range(user_api, value):
    before = _settings(user_api)["daily_application_limit"]
    assert _put(user_api, daily_application_limit=value).status_code == 422
    assert _settings(user_api)["daily_application_limit"] == before


@pytest.mark.parametrize("value", [-1, -0.01, 200.01, 1e9, "abc", None])
def test_threshold_rejects_invalid_values(user_api, value):
    assert _put(user_api, min_ctc_lpa=value).status_code == 422
    assert _settings(user_api)["min_ctc_lpa"] == 8.0


def test_threshold_rejects_nan_and_infinity(user_api):
    for literal in ("NaN", "Infinity"):
        body = json.dumps({**_settings(user_api), "min_ctc_lpa": 1}).replace(
            '"min_ctc_lpa": 1', f'"min_ctc_lpa": {literal}'
        )
        r = user_api.put(f"{V}/settings", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 422, literal


def test_threshold_edit_persists_across_restart_and_is_per_user(user_api):
    assert _put(user_api, min_ctc_lpa=12).json()["min_ctc_lpa"] == 12.0
    assert _put(user_api, min_ctc_lpa=0).status_code == 200 and _put(user_api, min_ctc_lpa=200).status_code == 200
    _put(user_api, min_ctc_lpa=12)
    restarted = make_client()
    restarted.post(f"{V}/auth/login", json={"email": "asha@example.com", "password": "correct-horse-42"})
    assert _settings(restarted)["min_ctc_lpa"] == 12.0
    other = make_client()
    register(other, email="ravi@example.com", name="Ravi K")
    assert _settings(other)["min_ctc_lpa"] == 8.0  # another user keeps the default


def test_api_tokens_cannot_change_policy(user_api):
    tok = user_api.post(f"{V}/auth/tokens", json={"name": "t", "scopes": ["read", "jobs:write"]}).json()["token"]
    from app.main import create_app

    c = TestClient(create_app(), headers={"Authorization": f"Bearer {tok}"})
    r = c.put(f"{V}/settings", json={**_settings(user_api), "daily_application_limit": 10_000, "min_ctc_lpa": 0})
    assert r.status_code == 403


def test_stored_out_of_range_limit_is_clamped(user_api):
    with session_factory()() as db:  # simulate a value written around the API (e.g. a manual DB edit)
        s = db.scalar(select(UserSettings))
        s.daily_application_limit = 10_001_000
        db.commit()
        assert app_svc.daily_limit(s.user) == MAX_DAILY_APPLICATIONS
        s.daily_application_limit = 0
        assert app_svc.daily_limit(s.user) == 1


def test_submission_is_refused_at_the_limit(user_api, monkeypatch):
    from tests.test_workflows import _onboard

    adapter = FakeAdapter()
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", adapter)
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    _put(user_api, daily_application_limit=10_000)
    job_id = user_api.get(f"{V}/jobs").json()["items"][0]["id"]
    aid = user_api.post(f"{V}/applications", json={"job_id": job_id}).json()["id"]
    detail = user_api.post(f"{V}/applications/{aid}/prepare", json={}).json()["application"]
    for a in detail["answers"]:
        if a["needs_review"]:
            user_api.put(f"{V}/applications/{aid}/answers/{a['id']}", json={"answer": "n/a"})
    ap = user_api.post(f"{V}/applications/{aid}/request-approval").json()
    user_api.post(f"{V}/approvals/{ap['id']}/decide", json={"approve": True})
    monkeypatch.setattr(app_svc, "submissions_last_24h", lambda db, uid: 10_000)
    r = user_api.post(f"{V}/applications/{aid}/submission", json={"action": "start"})
    assert r.status_code == 409 and "Daily limit of 10000" in r.json()["error"]["message"]
    monkeypatch.setattr(app_svc, "submissions_last_24h", lambda db, uid: 9_999)
    assert user_api.post(f"{V}/applications/{aid}/submission", json={"action": "start"}).status_code == 200


# ── Approval is human-only ────────────────────────────────────────────────────────────────────────
def test_service_refuses_non_web_approval(db):
    class _Req:
        status = "pending"

    with pytest.raises(app_svc.ApplicationError, match="only be decided by you"):
        app_svc.decide(db, None, _Req(), approve=True, via="api_token", actor="mcp")  # type: ignore[arg-type]


# ── Threshold changes: future evaluations vs. historical decisions ───────────────────────────────
def _verified_job(ext_id, title, lpa_min, lpa_max):
    j = job(ext_id, title, "Requirements\n• Python and PostgreSQL\n• 2-4 years of experience")
    j.salary_min, j.salary_max, j.salary_currency, j.salary_period = lpa_min * LAKH, lpa_max * LAKH, "INR", "year"
    j.salary_disclosure, j.salary_text = "verified", f"₹{lpa_min:g}–{lpa_max:g} LPA"
    return j


def test_threshold_change_affects_new_evaluations_and_preserves_history(user_api, monkeypatch):
    from tests.test_workflows import _onboard

    adapter = FakeAdapter(
        [_verified_job("10", "Backend Engineer", 10, 14), _verified_job("11", "Platform Engineer", 10, 14)]
    )
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", adapter)
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    jobs = {j["title"]: j for j in user_api.get(f"{V}/jobs").json()["items"]}
    saved, fresh_id = jobs["Backend Engineer"]["id"], jobs["Platform Engineer"]["id"]
    assert user_api.get(f"{V}/jobs/{saved}").json()["eligibility_detail"]["policy"]["min_ctc_lpa"] == 8.0
    user_api.put(f"{V}/jobs/{saved}/state", json={"state": "saved"})  # the user acted on this one

    _put(user_api, min_ctc_lpa=12)
    kept = user_api.get(f"{V}/jobs/{saved}").json()
    assert kept["eligibility_outdated"] is True
    assert kept["eligibility_detail"]["policy"]["min_ctc_lpa"] == 8.0  # historical decision kept as it was
    assert kept["eligibility_detail"]["verdict"] in ("ELIGIBLE", "MANUAL_REVIEW")
    new = user_api.get(f"{V}/jobs/{fresh_id}").json()
    assert new["eligibility_outdated"] is False and new["eligibility_detail"]["policy"]["min_ctc_lpa"] == 12.0
    assert check(new["eligibility_detail"], "SALARY")["code"] == "SAL_BELOW_THRESHOLD"

    again = user_api.post(f"{V}/jobs/{saved}/reevaluate").json()  # explicit re-evaluation
    assert again["eligibility_outdated"] is False
    assert again["eligibility_detail"]["policy"]["min_ctc_lpa"] == 12.0
    assert again["eligibility_detail"]["verdict"] == "REJECTED"
    with session_factory()() as db:
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "eligibility.reevaluated"))
        assert ev is not None and ev.meta["policy"]["min_ctc_lpa"] == 12.0
    assert user_api.post(f"{V}/jobs/reevaluate").json() == {"reevaluated": 0}


def test_users_with_different_thresholds_get_different_verdicts(monkeypatch):
    from tests.test_workflows import _onboard

    monkeypatch.setitem(
        providers.ADAPTERS, "greenhouse", FakeAdapter([_verified_job("20", "Backend Engineer", 10, 14)])
    )
    verdicts = {}
    for email, threshold in (("a@example.com", 8), ("b@example.com", 12)):
        api = make_client()
        register(api, email=email)
        _onboard(api)
        _put(api, min_ctc_lpa=threshold)
        api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
        item = api.get(f"{V}/jobs").json()["items"][0]
        verdicts[threshold] = api.get(f"{V}/jobs/{item['id']}").json()["eligibility_detail"]
    assert check(verdicts[8], "SALARY")["code"] == "SAL_ABOVE_THRESHOLD"
    assert check(verdicts[12], "SALARY")["code"] == "SAL_BELOW_THRESHOLD"
