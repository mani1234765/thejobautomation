"""Phase 3 M3: employment type, experience, work arrangement and section-aware skills extraction, and how the
new facts reach eligibility, scoring, storage and the API. Synthetic data only; no network."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from app.domain import job_parsing as jp
from app.domain import taxonomy
from app.domain.eligibility import employment_restricted
from app.domain.matching import MENTIONED_BONUS, CandidateProfile, compute_match
from app.domain.salary_policy import SalaryFacts, SalaryPolicy
from app.integrations.sources import providers
from app.integrations.sources.base import FetchResult, NormalizedJob
from app.models import Job
from tests.helpers import job
from tests.test_eligibility_policy import CAND, check, facts, verdict
from tests.test_workflows import _onboard

V = "/api/v1"
DATA = json.loads((Path(__file__).parent / "data" / "m3_fields_labelled.json").read_text())["cases"]
FIELDS = (
    "employment_type",
    "experience",
    "experience_preferred_min",
    "work_mode",
    "remote_region",
    "mandatory",
    "preferred",
)


def _extract(case: dict) -> dict:
    n = NormalizedJob(
        "greenhouse", "x", "Acme", case["title"], "https://x", None, "", case["description"], location=case["location"]
    ).enrich()
    return {
        "employment_type": n.employment_type,
        "experience": None if n.experience_min is None else [n.experience_min, n.experience_max],
        "experience_preferred_min": n.experience_preferred_min,
        "work_mode": n.work_mode,
        "remote_region": n.remote_region,
        "mandatory": sorted(n.required_skills),
        "preferred": sorted(n.preferred_skills),
    }


GOT = {c["id"]: _extract(c) for c in DATA}


@pytest.mark.parametrize("case", DATA, ids=[c["id"] for c in DATA])
def test_labelled_listing(case):
    got, want = GOT[case["id"]], case["expected"]
    for f in FIELDS:
        if f in case.get("known_misses", []):
            assert got[f] != want[f], f"{f} now matches the label: remove it from known_misses"
            continue
        assert got[f] == want[f], f


def _scalar(field: str, cases: list[dict]) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for c in cases:
        g, w = GOT[c["id"]][field], c["expected"][field]
        if g is not None and g == w:
            tp += 1
            continue
        fp += g is not None
        fn += w is not None
    return tp, fp, fn


def _sets(field: str, cases: list[dict]) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for c in cases:
        g, w = set(GOT[c["id"]][field]), set(c["expected"][field])
        tp, fp, fn = tp + len(g & w), fp + len(g - w), fn + len(w - g)
    return tp, fp, fn


def test_dataset_metrics_per_field():
    """Precision and recall per field on the whole labelled set (54 synthetic listings; see the dataset's
    _about for the untuned first-run figures). Documents the aggregate; per-case regressions fail above."""
    assert len(DATA) == 54
    got = {f: (_sets if f in ("mandatory", "preferred") else _scalar)(f, DATA) for f in FIELDS}
    assert got == {
        "employment_type": (26, 0, 0),
        "experience": (37, 0, 0),
        "experience_preferred_min": (3, 0, 0),
        "work_mode": (42, 1, 0),
        "remote_region": (12, 0, 0),
        "mandatory": (74, 0, 0),
        "preferred": (24, 0, 1),
    }


# ── M3a taxonomy hygiene ───────────────────────────────────────────────────────────────────────
def test_no_alias_maps_to_two_skills():
    seen: dict[str, set[str]] = {}
    for s in taxonomy.SKILLS:
        for a in (s.name, *s.aliases, *s.case_sensitive_aliases):
            seen.setdefault(taxonomy._norm(a), set()).add(s.name)
    assert {a: n for a, n in seen.items() if len(n) > 1} == {}
    names = {s.name for s in taxonomy.SKILLS}
    assert all(r in names for s in taxonomy.SKILLS for r in s.related)
    assert set(taxonomy.LISTING_IGNORED) <= names and set(taxonomy.LISTING_CASE_SENSITIVE) <= names


@pytest.mark.parametrize(
    "text",
    [
        "Join GitLab, the DevSecOps platform.",
        "Our leadership team values communication.",
        "Security is in our DNA; we use analytics everywhere.",
        "You will excel in a fast-paced team and react quickly to the rest of the team.",
        "A swift hiring process this spring.",
    ],
)
def test_listing_boilerplate_is_not_a_skill(text):
    assert taxonomy.extract_skills(text, listing=True) == {}


def test_listing_still_reads_real_skills():
    found = taxonomy.extract_skills("Experience with React, Excel, Spring Boot, REST APIs and GitLab CI", listing=True)
    assert set(found) == {"React", "Excel", "Spring", "REST APIs", "CI/CD"}


def test_resume_and_profile_matching_is_unchanged():
    # What users type and what résumés say keep every alias, so stored profiles canonicalise exactly as before.
    assert taxonomy.canonicalize("GitHub") == "Git" and taxonomy.canonicalize("analytics") == "Data Analysis"
    assert "Leadership" in taxonomy.extract_skills("Leadership: mentored 4 engineers")


# ── M3b section-aware skills ───────────────────────────────────────────────────────────────────
JD = (
    "About us\nAcme builds Kubernetes tooling with a security-first, data analytics culture.\n"
    "Responsibilities\n• Maintain our Terraform modules\n"
    "Requirements\n• Python and PostgreSQL\n• Excellent communication skills\n"
    "Nice to have\n• Go\n• Docker is required for on-call work\n"
    "Benefits\n• Annual bonus\n• AWS certification budget\n"
    "We are an equal opportunity employer and value diversity, including Java developers.\n"
)


def test_sections_tiers_and_evidence():
    s = jp.split_skills(JD)
    assert sorted(s.required) == ["Docker", "PostgreSQL", "Python"]  # "is required" overrides "Nice to have"
    assert sorted(s.preferred) == ["Communication", "Go"]  # soft skills are at most preferred
    assert s.mentioned == ["Terraform"]  # responsibilities: mentioned only
    for absent in ("Kubernetes", "Security", "Data Analysis", "AWS", "Java"):  # about / benefits / EEO
        assert absent not in s.required + s.preferred + s.mentioned
    ev = s.evidence["Docker"]
    assert ev["tier"] == "mandatory" and ev["section"] == "Nice to have" and ev["term"] == "Docker"
    assert ev["quote"] == "“Docker is required for on-call work”"


def test_without_headings_or_wording_skills_are_only_mentioned():
    s = jp.split_skills("We build with Python, Django and PostgreSQL on AWS.")
    assert s.required == [] and s.preferred == [] and sorted(s.mentioned) == ["AWS", "Django", "PostgreSQL", "Python"]


@pytest.mark.parametrize(
    ("text", "required", "preferred", "mentioned"),
    [
        ("Python is required, Go is a plus.", ["Python"], ["Go"], []),
        ("Must have Python, Django; nice to have Go.", ["Django", "Python"], ["Go"], []),
        ("Kubernetes experience is not required.", [], [], ["Kubernetes"]),
        ("Preferred: Rust. Required: SQL.", ["SQL"], ["Rust"], []),
    ],
)
def test_sentence_wording(text, required, preferred, mentioned):
    s = jp.split_skills(text)
    assert (sorted(s.required), sorted(s.preferred), sorted(s.mentioned)) == (required, preferred, mentioned)


def test_mentioned_skills_never_reject_and_add_a_small_bonus():
    only_mentioned = facts(required_skills=[], preferred_skills=[], mentioned_skills=["Rust", "Go", "Elixir"])
    e = verdict(only_mentioned)
    assert (
        check(e, "SKILLS")["outcome"] == "unknown" and "mentions Rust, Go, Elixir" in check(e, "SKILLS")["explanation"]
    )
    partial = {"required_skills": ["Python", "Go"]}  # CAND lacks Go, so the score has room for the bonus
    with_hit = facts(**partial, mentioned_skills=["PostgreSQL", "Rust"])  # CAND has PostgreSQL
    base = compute_match(facts(**partial), CAND, SalaryPolicy()).score
    boosted = compute_match(with_hit, CAND, SalaryPolicy())
    assert 0 < boosted.score - base <= MENTIONED_BONUS and boosted.mentioned_matched == ["PostgreSQL"]
    assert "Rust" not in boosted.missing_required + boosted.missing_preferred
    assert check(boosted.eligibility, "SKILLS")["code"] == check(verdict(facts(**partial)), "SKILLS")["code"]


# ── M3c employment type ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("title", "hint", "text", "want"),
    [
        ("Engineer", "Full-time", "", "full_time"),
        ("Engineer", "Temporary", "", "temporary"),  # board field: no longer folded into contract
        ("Engineer", "Freelance", "", "freelance"),
        ("Engineer", None, "This is an apprenticeship programme.", "apprenticeship"),
        ("Engineer", None, "Employment type: Part-time", "part_time"),
        ("Engineer", None, "A 6-month contract role.", "contract"),
        ("Engineer", None, "Full-time internship for students.", "internship"),
        ("Engineer", None, "Full-time or part-time.", None),  # contradictory: unknown
        ("Engineer", None, "Full-time or contract options available.", None),
        ("Engineer", None, "Strong performers may convert to a full-time role.", None),
        ("Engineer", None, "Benefits\n• Full-time employees get health cover", None),  # boilerplate
        ("Account Executive", None, "Articulate contractual and financial value; manage freelancers.", None),
        ("Software Engineer Intern", None, "This is a full-time position.", "internship"),  # title wins
    ],
)
def test_employment_type(title, hint, text, want):
    e = jp.detect_employment(title, hint, text)
    assert e.value == want and e.is_internship == (want == "internship")


def test_contradictory_type_is_unclear_not_silent():
    j = facts(employment_type=None, description="We're hiring full-time or part-time.")
    restricted = check(verdict(j), "EMPLOYMENT")
    assert restricted["outcome"] == "review" and restricted["code"] == "EMPLOYMENT_UNCLEAR"
    assert restricted["evidence"] == ["“We're hiring full-time or part-time”"]


def test_new_types_are_judged_against_preferences():
    freelance = facts(employment_type="freelance")
    assert check(verdict(freelance), "EMPLOYMENT")["code"] == "EMPLOYMENT_MISMATCH"  # CAND wants full-time only
    wants = CandidateProfile(**{**CAND.__dict__, "employment_types": ["full_time", "freelance"]})
    assert check(verdict(freelance, wants), "EMPLOYMENT")["code"] == "EMPLOYMENT_MATCH"


LEGACY_FOUR = ["full_time", "part_time", "contract", "internship"]


def _employment_outcome(types: list[str], et: str) -> tuple[str, float | None]:
    cand = CandidateProfile(**{**CAND.__dict__, "employment_types": types})
    r = compute_match(facts(employment_type=et, is_internship=et == "internship"), cand, SalaryPolicy())
    return check(r.eligibility, "EMPLOYMENT")["code"], next(f for f in r.factors if f.key == "employment").score


@pytest.mark.parametrize("et", ["freelance", "temporary", "apprenticeship"])
def test_four_original_types_exclude_the_new_ones(et):
    """A selection is never widened: exactly the four original types (saved before M3 or chosen after it)
    accepts those four and rejects freelance, temporary and apprenticeship."""
    assert employment_restricted(LEGACY_FOUR)
    assert _employment_outcome(LEGACY_FOUR, et) == ("EMPLOYMENT_MISMATCH", 0.0)
    assert all(_employment_outcome(LEGACY_FOUR, t) == ("EMPLOYMENT_MATCH", 1.0) for t in LEGACY_FOUR)


def test_all_seven_accept_everything_and_empty_means_no_preference():
    seven = list(jp.EMPLOYMENT_TYPES)
    assert not employment_restricted(seven) and not employment_restricted([])
    for et in seven:
        assert _employment_outcome(seven, et) == ("EMPLOYMENT_NO_PREFERENCE", 1.0)
        code, score = _employment_outcome([], et)
        assert code == "EMPLOYMENT_NO_PREFERENCE" and score is None  # factor unscored without a preference
    assert _employment_outcome(["full_time"], "freelance") == ("EMPLOYMENT_MISMATCH", 0.0)


def test_saved_four_type_selection_is_kept_and_applied_as_saved(user_api, db, monkeypatch):
    """An existing user's stored preference is read back unchanged (no rewrite), and a new selection of the
    same four types behaves identically."""
    from app.models import UserProfile

    _onboard(user_api)
    prof = user_api.get(f"{V}/profile").json()["profile"]
    prof["employment_types"] = LEGACY_FOUR
    assert user_api.put(f"{V}/profile", json=prof).status_code == 200
    stored = db.scalar(select(UserProfile))
    db.refresh(stored)
    assert stored.employment_types == LEGACY_FOUR
    assert user_api.get(f"{V}/profile").json()["profile"]["employment_types"] == LEGACY_FOUR
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", Board("Freelance engagement.\nRequirements\n• Python"))
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    jid = user_api.get(f"{V}/jobs").json()["items"][0]["id"]
    d = user_api.get(f"{V}/jobs/{jid}").json()
    assert d["employment_type"] == "freelance"
    emp = next(c for c in d["eligibility_detail"]["checks"] if c["rule"] == "EMPLOYMENT")
    assert emp["code"] == "EMPLOYMENT_MISMATCH"


def test_profile_accepts_all_seven_types_and_keeps_saved_ones(user_api):
    _onboard(user_api)
    prof = user_api.get(f"{V}/profile").json()["profile"]
    assert prof["employment_types"] == ["full_time"]  # an existing preference is read back unchanged
    prof["employment_types"] = list(jp.EMPLOYMENT_TYPES)
    assert user_api.put(f"{V}/profile", json=prof).status_code == 200
    assert user_api.get(f"{V}/profile").json()["profile"]["employment_types"] == list(jp.EMPLOYMENT_TYPES)
    prof["employment_types"] = ["gig"]
    assert user_api.put(f"{V}/profile", json=prof).status_code == 422


# ── M3d experience ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "minimum", "maximum", "preferred"),
    [
        ("Freshers welcome.", 0, None, None),
        ("No prior experience required.", 0, None, None),
        ("3+ years of experience preferred.", None, None, 3),
        ("2+ years of experience required. Ideally 5+ years.", 2, None, 5),
        ("Founded 15 years ago. Requirements\n• 4+ years building APIs", 4, None, None),
        ("Python 3 and Java 17 in production since 2019.", None, None, None),
        ("At least 8 years of experience, including 2 years managing teams.", 8, None, None),
        ("Experience: 3-5 yrs", 3, 5, None),
        ("Over the past 10 years we have grown fast.", None, None, None),
    ],
)
def test_experience(text, minimum, maximum, preferred):
    x = jp.experience(text)
    assert (x.minimum, x.maximum, x.preferred_minimum) == (minimum, maximum, preferred)


def test_preferred_experience_never_rejects():
    j = facts(experience_min=None, experience_max=None, description="5+ years of experience preferred.")
    junior = CandidateProfile(**{**CAND.__dict__, "years_experience": 0.5})
    c = check(verdict(j, junior), "EXPERIENCE")
    assert c["outcome"] == "unknown" and "prefers 5+ years" in c["explanation"]
    assert c["evidence"] == ["“5+ years of experience preferred”"]


# ── M3e work arrangement ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("location", "text", "mode"),
    [
        (None, "This role is not remote; you will work on-site.", "onsite"),
        (None, "Remote troubleshooting experience preferred.", None),
        (None, "Work from office, 5 days a week.", "onsite"),
        (None, "WFH allowed on Fridays.", "hybrid"),
        (None, "We are a remote-friendly team, but this role is office-based.", "onsite"),
        (None, "Remote-first company.", "remote"),
        (None, "Hybrid: 2 days WFO.", "hybrid"),
        (None, "In-office perks: lunch and snacks. Meet customers onsite.", None),
        ("Remote", "This role is hybrid, 3 days in office.", "hybrid"),  # the role statement beats the field
        (None, "Remote on Fridays, office-based otherwise.", "hybrid"),
        (None, "Occasional remote work is possible; this role is on-site.", "onsite"),
        (None, "This role is on-site with occasional remote days.", "onsite"),
        (None, "Remote-first company, this role is office-based.", "onsite"),
        ("London or Remote (UK)", "", "remote"),
        # Two alternative arrangements can't be stored, so this stays unknown (review if the user has a preference).
        ("London, UK", "Hybrid in London or fully remote within the UK.", None),
    ],
)
def test_work_arrangement(location, text, mode):
    assert jp.work_arrangement(location, text).mode == mode


@pytest.mark.parametrize(
    ("location", "text", "region"),
    [
        ("Remote - India", "", "India"),
        ("Remote (US)", "", "United States"),
        ("Remote - EMEA", "", "EMEA"),
        ("London or Remote (UK)", "", "United Kingdom"),
        ("Remote", "Remote within India only.", "India"),
        ("Remote", "Open to candidates in North America or Latin America.", "North America, Latin America"),
        ("Remote", "Join us remotely from anywhere.", None),  # "us" is not the United States
        ("Bengaluru", "Hybrid, India.", None),  # regions are captured for remote roles only
    ],
)
def test_remote_region(location, text, region):
    assert jp.work_arrangement(location, text).region == region


def test_remote_region_is_informational_only():
    remote = facts(work_mode="remote", location="Remote", description="Remote within the United States only.")
    e = verdict(remote)
    assert (
        check(e, "LOCATION")["code"] == "LOCATION_REMOTE_OK"
        and e["verdict"] == verdict(facts(work_mode="remote", location="Remote", description="Remote."))["verdict"]
    )


# ── Apprenticeship compensation ────────────────────────────────────────────────────────────────
APPR = SalaryPolicy(apprentice_min_stipend=20_000, apprentice_stipend_currency="INR", apprentice_stipend_period="month")


def pay(lo, hi=None, cur="INR", per="month", disclosure="verified", text=None) -> SalaryFacts:
    return SalaryFacts(False, disclosure, lo, lo if hi is None else hi, cur, per, text=text)


def _pay_check(description: str, salary: SalaryFacts, policy: SalaryPolicy = APPR) -> dict:
    j = facts(
        employment_type="apprenticeship",
        is_internship=False,
        seniority="junior",
        salary=salary,
        description=description,
    )
    e = compute_match(j, CAND, policy).eligibility
    return next(c for c in e["checks"] if c["rule"] in ("SALARY", "STIPEND", "COMPENSATION"))


@pytest.mark.parametrize(
    ("amount", "code", "outcome"),
    [
        (25_000, "APPRENTICE_STIPEND_MEETS_MINIMUM", "pass"),
        (20_000, "APPRENTICE_STIPEND_MEETS_MINIMUM", "pass"),  # meets or exceeds
        (19_999, "APPRENTICE_STIPEND_BELOW_MINIMUM", "reject"),
    ],
)
def test_structured_stipend_against_the_apprenticeship_minimum(amount, code, outcome):
    c = _pay_check("Paid apprenticeship with a monthly stipend.", pay(amount))
    assert (c["rule"], c["code"], c["outcome"]) == ("STIPEND", code, outcome)
    d = c["details"]
    assert (d["compensation_type"], d["source"], d["verification"]) == (
        "stipend",
        "structured field",
        "EMPLOYER_VERIFIED",
    )
    assert (d["amount_min"], d["currency"], d["period"], d["reason_code"]) == (amount, "INR", "month", code)
    assert d["wording_quote"] == "“Paid apprenticeship with a monthly stipend”"
    assert d["policy"]["applied"] == "apprenticeship stipend minimum" and d["policy"]["minimum"] == 20_000
    assert c["evidence"][0].startswith("Employer-provided stipend · ")


@pytest.mark.parametrize(
    ("amount", "code", "outcome"),
    [
        (25_000, "APPRENTICE_STIPEND_MEETS_MINIMUM_ADVERTISED", "pass"),
        (20_000, "APPRENTICE_STIPEND_MEETS_MINIMUM_ADVERTISED", "pass"),
        (15_000, "APPRENTICE_STIPEND_BELOW_MINIMUM", "reject"),
    ],
)
def test_description_only_stipend_is_compared_but_labelled_unverified(amount, code, outcome):
    c = _pay_check("Stipend provided.", pay(amount, disclosure="advertised", text=f"₹{amount:,} per month"))
    assert (c["rule"], c["code"], c["outcome"]) == ("STIPEND", code, outcome)
    d = c["details"]
    assert (d["source"], d["verification"]) == ("job description", "ADVERTISED_UNVERIFIED")
    assert c["evidence"][0].startswith("Advertised stipend · from the description, unverified · ")
    assert d["pay_quote"] == f"“₹{amount:,} per month”"


def test_description_only_stipend_end_to_end_from_the_listing():
    n = job("a1", "Software Apprentice", "Our apprenticeship pays a monthly stipend of ₹25,000 per month.")
    assert (n.employment_type, n.salary_disclosure, n.salary_min, n.salary_period) == (
        "apprenticeship",
        "advertised",
        25_000,
        "month",
    )
    j = facts(
        title=n.title,
        employment_type=n.employment_type,
        is_internship=False,
        seniority=n.seniority,
        salary=SalaryFacts(
            False, n.salary_disclosure, n.salary_min, n.salary_max, n.salary_currency, n.salary_period, n.salary_text
        ),
        description=n.description_text,
    )
    c = check(compute_match(j, CAND, APPR).eligibility, "STIPEND")
    assert (
        c["code"] == "APPRENTICE_STIPEND_MEETS_MINIMUM_ADVERTISED"
        and c["details"]["verification"] == "ADVERTISED_UNVERIFIED"
    )


def test_user_choices_for_advertised_stipends():
    advertised = pay(25_000, disclosure="advertised")
    review_all = replace(APPR, apprentice_advertised_stipend="review")
    assert _pay_check("Stipend provided.", advertised, review_all)["code"] == "APPRENTICE_STIPEND_ADVERTISED_REVIEW"
    review_passing = replace(APPR, apprentice_unverified_outcome="review")
    c = _pay_check("Stipend provided.", advertised, review_passing)
    assert (c["code"], c["outcome"]) == ("APPRENTICE_STIPEND_UNVERIFIED_REVIEW", "review")
    below = _pay_check("Stipend provided.", pay(15_000, disclosure="advertised"), review_passing)
    assert below["outcome"] == "reject"  # below the minimum is rejected either way
    assert _pay_check("Stipend provided.", advertised, replace(APPR, apprentice_min_stipend=None))["code"] == (
        "APPRENTICE_STIPEND_NO_MINIMUM"
    )


@pytest.mark.parametrize(
    ("salary", "policy", "code"),
    [
        (
            pay(20_000),
            replace(APPR, apprentice_min_stipend=240_000, apprentice_stipend_period="year"),
            "APPRENTICE_STIPEND_MEETS_MINIMUM",
        ),
        (pay(230_000, per="year"), APPR, "APPRENTICE_STIPEND_BELOW_MINIMUM"),  # ₹19,167/month
        (pay(240_000, per="year"), APPR, "APPRENTICE_STIPEND_MEETS_MINIMUM"),
        (pay(500, per="hour"), APPR, "APPRENTICE_STIPEND_PERIOD_UNSUPPORTED"),
        (pay(25_000, per=None), APPR, "APPRENTICE_STIPEND_PERIOD_UNSUPPORTED"),
    ],
)
def test_monthly_and_yearly_periods(salary, policy, code):
    assert _pay_check("Stipend provided.", salary, policy)["code"] == code


@pytest.mark.parametrize(
    ("cur", "code"),
    [
        (None, "APPRENTICE_STIPEND_CURRENCY_UNKNOWN"),
        ("$", "APPRENTICE_STIPEND_CURRENCY_UNKNOWN"),
        ("USD", "APPRENTICE_STIPEND_FX_UNAVAILABLE"),
    ],
)
def test_stipend_currency_is_never_assumed(cur, code):
    c = _pay_check("Stipend provided.", pay(400, cur=cur), APPR)
    assert (c["code"], c["outcome"]) == (code, "review")
    assert c["details"]["currency_identified"] is (cur == "USD")


def test_apprenticeship_salary_wording_uses_the_salary_threshold():
    c = _pay_check("Apprenticeship salary: ₹3 LPA.", pay(300_000, per="year"))
    assert (c["rule"], c["code"], c["outcome"]) == ("SALARY", "SAL_BELOW_THRESHOLD", "reject")
    assert c["details"]["policy"] == {
        "applied": "salary threshold",
        "min_ctc_lpa": 8.0,
        "allow_undisclosed_salary": True,
    }
    assert _pay_check("CTC ₹8 LPA.", pay(800_000, per="year"))["code"] == "SAL_AT_THRESHOLD"  # strictly greater
    assert _pay_check("Wages ₹25,000 a month.", pay(25_000, disclosure="advertised"))["code"] == "SAL_UNVERIFIED"


@pytest.mark.parametrize(
    "description", ["Paid apprenticeship, ₹25,000 per month.", "Salary or stipend depending on the track."]
)
def test_apprenticeship_pay_without_clear_wording_needs_review(description):
    c = _pay_check(description, pay(25_000))
    assert (c["rule"], c["code"], c["outcome"]) == ("COMPENSATION", "APPRENTICESHIP_PAY_TYPE_UNCLEAR", "review")
    assert "neither your salary threshold nor your stipend minimum was applied" in c["explanation"]
    assert c["missing"] == ["compensation type"] and c["details"]["policy"] == {"applied": "none"}


def test_apprenticeship_missing_pay():
    none = SalaryFacts(False, "undisclosed", None, None, None, None)
    c = _pay_check("Paid apprenticeship.", none)  # both policies accept undisclosed pay by default
    assert (c["rule"], c["code"], c["outcome"]) == ("COMPENSATION", "APPRENTICESHIP_PAY_UNDISCLOSED", "pass")
    assert c["details"]["verification"] == "NOT_DISCLOSED"
    strict = replace(APPR, allow_undisclosed_stipend=False)
    assert _pay_check("Paid apprenticeship.", none, strict)["code"] == "APPRENTICESHIP_PAY_TYPE_UNCLEAR"
    assert _pay_check("A stipend will be paid.", none, strict)["code"] == "APPRENTICE_STIPEND_UNDISCLOSED_REVIEW"
    assert _pay_check("A stipend will be paid.", none)["code"] == "APPRENTICE_STIPEND_UNDISCLOSED"


def test_boilerplate_wording_is_ignored_and_internships_are_unchanged():
    benefits = "Benefits\n• Competitive salary reviews every year"
    assert _pay_check(benefits, pay(25_000))["code"] == "APPRENTICESHIP_PAY_TYPE_UNCLEAR"
    for disclosure, code in (("verified", "STIPEND_MEETS_MINIMUM"), ("advertised", "STIPEND_UNVERIFIED")):
        intern = facts(
            employment_type="internship",
            is_internship=True,
            salary=SalaryFacts(True, disclosure, 25_000, 25_000, "INR", "month"),
            description="Stipend ₹25,000 per month.",
        )
        policy = replace(APPR, min_stipend_monthly_inr=20_000)  # the internship minimum, not the apprenticeship one
        c = check(compute_match(intern, CAND, policy).eligibility, "STIPEND")
        assert c["code"] == code and c["details"] == {}  # Phase 2 internship rules, unchanged


def test_apprenticeship_settings_api_validation_and_older_clients(user_api):
    _onboard(user_api)
    s = user_api.get(f"{V}/settings").json()
    assert {k: s[k] for k in s if k.startswith("apprentice_")} == {
        "apprentice_min_stipend": None,
        "apprentice_stipend_currency": "INR",
        "apprentice_stipend_period": "month",
        "apprentice_advertised_stipend": "compare",
        "apprentice_unverified_outcome": "pass",
    }
    body = {k: v for k, v in s.items() if k != "normalized"}
    ok = {
        **body,
        "apprentice_min_stipend": 300_000,
        "apprentice_stipend_currency": "EUR",
        "apprentice_stipend_period": "year",
    }
    assert user_api.put(f"{V}/settings", json=ok).status_code == 200
    for bad in (
        {"apprentice_stipend_currency": "XYZ"},
        {"apprentice_stipend_currency": None},
        {"apprentice_stipend_period": "week"},
        {"apprentice_min_stipend": -1},
        {"apprentice_advertised_stipend": "accept"},
        {"apprentice_unverified_outcome": "reject"},
    ):
        assert user_api.put(f"{V}/settings", json={**ok, **bad}).status_code == 422, bad
    legacy = {k: v for k, v in body.items() if not k.startswith("apprentice_")}  # a client from before M3
    assert user_api.put(f"{V}/settings", json=legacy).status_code == 200
    kept = user_api.get(f"{V}/settings").json()
    assert (kept["apprentice_min_stipend"], kept["apprentice_stipend_currency"], kept["apprentice_stipend_period"]) == (
        300_000,
        "EUR",
        "year",
    )


def test_invalid_stored_currency_is_reported_not_guessed(user_api, db):
    from app.models import UserSettings

    _onboard(user_api)
    row = db.scalar(select(UserSettings))
    row.apprentice_min_stipend, row.apprentice_stipend_currency = 20_000, "???"  # e.g. edited by hand
    db.commit()
    s = user_api.get(f"{V}/settings").json()
    assert s["apprentice_stipend_currency"] is None
    assert [n["field"] for n in s["normalized"]] == ["apprentice_stipend_currency"]
    policy = replace(APPR, apprentice_stipend_currency=None)
    assert _pay_check("Stipend provided.", pay(25_000), policy)["code"] == "APPRENTICE_POLICY_CURRENCY_UNSET"


class ApprenticeBoard:
    provider = "greenhouse"

    def fetch(self, identifier):
        text = "Our apprenticeship pays a monthly stipend.\nRequirements\n• Python"
        n = job("ap1", "Software Apprentice", text)
        n.salary_min = n.salary_max = 25_000
        n.salary_currency, n.salary_period, n.salary_disclosure = "INR", "month", "verified"
        return FetchResult("Acme Payments", [n, job("ap2", "Data Apprentice", text)], raw_count=2)


def test_changing_apprenticeship_settings_marks_saved_decisions_outdated(user_api, monkeypatch):
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", ApprenticeBoard())
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    ids = {i["title"]: i["id"] for i in user_api.get(f"{V}/jobs").json()["items"]}
    saved, other = ids["Software Apprentice"], ids["Data Apprentice"]
    user_api.put(f"{V}/jobs/{saved}/state", json={"state": "saved"})
    before = user_api.get(f"{V}/jobs/{saved}").json()
    c = next(c for c in before["eligibility_detail"]["checks"] if c["rule"] == "STIPEND")
    assert c["code"] == "APPRENTICE_STIPEND_NO_MINIMUM" and not before["eligibility_outdated"]
    s = user_api.get(f"{V}/settings").json()
    s = {k: v for k, v in s.items() if k != "normalized"} | {"apprentice_min_stipend": 30_000}
    assert user_api.put(f"{V}/settings", json=s).status_code == 200
    kept = user_api.get(f"{V}/jobs/{saved}").json()
    assert kept["eligibility_outdated_reasons"] == ["policy"]
    assert kept["eligibility_policy_changes"] == [
        {"key": "apprentice_min_stipend", "group": "apprenticeship", "before": None, "after": 30_000}
    ]
    assert kept["eligibility_detail"]["evaluated_at"] == before["eligibility_detail"]["evaluated_at"]  # not recomputed
    assert next(c for c in kept["eligibility_detail"]["checks"] if c["rule"] == "STIPEND")["code"] == (
        "APPRENTICE_STIPEND_NO_MINIMUM"
    )
    fresh_other = user_api.get(f"{V}/jobs/{other}").json()  # not acted on: recomputed under the new setting
    assert not fresh_other["eligibility_outdated"]
    fresh = user_api.post(f"{V}/jobs/{saved}/reevaluate").json()
    c = next(c for c in fresh["eligibility_detail"]["checks"] if c["rule"] == "STIPEND")
    assert c["code"] == "APPRENTICE_STIPEND_BELOW_MINIMUM" and fresh["eligibility_outdated"] is False
    assert fresh["eligibility_detail"]["policy"]["apprentice_min_stipend"] == 30_000
    assert fresh["eligibility_policy_changes"] == []


def test_salary_setting_changes_are_listed_separately_from_apprenticeship_ones(user_api, monkeypatch):
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", ApprenticeBoard())
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    saved = next(i["id"] for i in user_api.get(f"{V}/jobs").json()["items"] if i["title"] == "Software Apprentice")
    user_api.put(f"{V}/jobs/{saved}/state", json={"state": "saved"})
    s = {k: v for k, v in user_api.get(f"{V}/settings").json().items() if k != "normalized"}
    changed = s | {"min_ctc_lpa": s["min_ctc_lpa"] + 4, "allow_undisclosed_salary": False}
    assert user_api.put(f"{V}/settings", json=changed).status_code == 200
    kept = user_api.get(f"{V}/jobs/{saved}").json()
    assert kept["eligibility_outdated_reasons"] == ["policy"]
    assert kept["eligibility_policy_changes"] == [
        {"key": "min_ctc_lpa", "group": "salary", "before": s["min_ctc_lpa"], "after": s["min_ctc_lpa"] + 4},
        {"key": "allow_undisclosed_salary", "group": "salary", "before": True, "after": False},
    ]
    # Changing it back makes the kept decision current again; nothing was recomputed in between.
    assert user_api.put(f"{V}/settings", json=s).status_code == 200
    back = user_api.get(f"{V}/jobs/{saved}").json()
    assert not back["eligibility_outdated"] and back["eligibility_policy_changes"] == []
    assert back["eligibility_detail"]["evaluated_at"] == kept["eligibility_detail"]["evaluated_at"]


def test_apprenticeship_settings_are_per_user(user_api, monkeypatch):
    """One user's stipend settings never mark another user's saved decision outdated or change its policy."""
    from tests.conftest import make_client, register

    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", ApprenticeBoard())
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    saved = next(i["id"] for i in user_api.get(f"{V}/jobs").json()["items"] if i["title"] == "Software Apprentice")
    user_api.put(f"{V}/jobs/{saved}/state", json={"state": "saved"})
    mine = user_api.get(f"{V}/settings").json()

    other = make_client()
    register(other, email="ravi@example.com")
    s = {k: v for k, v in other.get(f"{V}/settings").json().items() if k != "normalized"}
    assert other.put(f"{V}/settings", json=s | {"apprentice_min_stipend": 50_000}).status_code == 200

    assert user_api.get(f"{V}/settings").json() == mine
    kept = user_api.get(f"{V}/jobs/{saved}").json()
    assert not kept["eligibility_outdated"] and kept["eligibility_policy_changes"] == []
    assert kept["eligibility_detail"]["policy"]["apprentice_min_stipend"] is None


def test_policy_changes_treat_missing_apprenticeship_keys_as_defaults(monkeypatch):
    """A decision stored before M3 has no apprenticeship keys; they count as the defaults, so only settings
    the user actually changed are listed."""
    from types import SimpleNamespace

    from app.services import jobs as svc

    current = {
        "min_ctc_lpa": 8.0,
        "allow_undisclosed_salary": True,
        "min_stipend_monthly_inr": None,
        "allow_undisclosed_stipend": True,
        **svc.APPRENTICE_DEFAULTS,
        "apprentice_stipend_period": "year",
        "engine": "x",
        "fx": None,
    }
    pre_m3 = {k: current[k] for k in ("min_ctc_lpa", "allow_undisclosed_salary", "min_stipend_monthly_inr")}
    pre_m3 |= {"allow_undisclosed_stipend": True, "engine": "old"}
    match = SimpleNamespace(eligibility={"policy": pre_m3})
    monkeypatch.setattr(svc, "policy_snapshot", lambda user, fxc=None: current)
    assert svc.policy_changes(None, match) == [
        {"key": "apprentice_stipend_period", "group": "apprenticeship", "before": "month", "after": "year"}
    ]
    assert svc.policy_changes(None, SimpleNamespace(eligibility={})) == []
    assert svc.policy_changes(None, None) == []


def test_default_apprenticeship_settings_do_not_change_the_policy_identity():
    from app.services.jobs import _policy_key

    old = {
        "min_ctc_lpa": 8.0,
        "allow_undisclosed_salary": True,
        "min_stipend_monthly_inr": None,
        "allow_undisclosed_stipend": True,
    }
    from app.domain.salary_policy import APPRENTICE_DEFAULTS

    assert _policy_key({**old, **APPRENTICE_DEFAULTS, "engine": "x"}) == _policy_key(old)
    assert _policy_key({**old, **APPRENTICE_DEFAULTS, "apprentice_min_stipend": 1}) != _policy_key(old)


# ── M3f storage, API and decisions ─────────────────────────────────────────────────────────────
class Board:
    provider = "greenhouse"

    def __init__(self, text):
        self.text = text

    def fetch(self, identifier):
        return FetchResult(
            "Acme Payments", [job("1", "Backend Engineer", self.text, location="Remote - India")], raw_count=1
        )


def test_new_facts_are_stored_and_shown_with_evidence(user_api, monkeypatch, db):
    text = (
        "This role is fully remote within India.\nRequirements\n• 3+ years of experience with Python\n"
        "Nice to have\n• Kubernetes\nResponsibilities\n• Maintain Terraform\n4+ years preferred."
    )
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", Board(text))
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    item = user_api.get(f"{V}/jobs").json()["items"][0]
    d = user_api.get(f"{V}/jobs/{item['id']}").json()
    assert (d["required_skills"], d["preferred_skills"], d["mentioned_skills"]) == (
        ["Python"],
        ["Kubernetes"],
        ["Terraform"],
    )
    assert (d["experience_min"], d["experience_preferred_min"], d["remote_region"]) == (3, 4, "India")
    ev = d["field_evidence"]
    assert ev["skills"]["Python"]["quote"] == "“3+ years of experience with Python”"
    assert ev["remote_region"] == "Location: “Remote - India”" and ev["work_mode"].startswith(
        "“This role is fully remote"
    )
    stored = db.scalar(select(Job))
    assert stored.mentioned_skills == ["Terraform"] and stored.remote_region == "India"


def test_jobs_stored_before_m3_keep_their_listing_identity(user_api, monkeypatch, db):
    """A job stored before M3 has no mentioned skills (NULL). Its saved decision is outdated only because the
    engine changed, never because "the job listing changed"."""
    from app.domain import matching
    from app.services import jobs as jobs_svc

    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", Board("Requirements\n• Python"))
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    jid = user_api.get(f"{V}/jobs").json()["items"][0]["id"]
    j = db.get(Job, jid)
    j.mentioned_skills, j.field_evidence = None, None  # as a pre-M3 row looks after the migration
    db.commit()
    user_api.put(f"{V}/jobs/{jid}/state", json={"state": "saved"})
    user_api.post(f"{V}/jobs/{jid}/reevaluate")
    before = user_api.get(f"{V}/jobs/{jid}").json()
    assert before["eligibility_outdated"] is False and before["mentioned_skills"] is None
    monkeypatch.setattr(matching, "SCORING_VERSION", "2099.1.1")
    monkeypatch.setattr(jobs_svc, "SCORING_VERSION", "2099.1.1")
    after = user_api.get(f"{V}/jobs/{jid}").json()
    assert after["eligibility_outdated_reasons"] == ["engine"]
    assert after["eligibility_detail"]["evaluated_at"] == before["eligibility_detail"]["evaluated_at"]  # kept
