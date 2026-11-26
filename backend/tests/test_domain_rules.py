import pytest

from app.domain import job_parsing as jp
from app.domain import taxonomy
from app.domain.application_state import InvalidTransition, check_transition
from app.domain.matching import CandidateProfile, JobFacts, compute_match
from app.domain.salary_policy import SalaryFacts, SalaryPolicy, evaluate

POLICY = SalaryPolicy()


def sf(**kw):
    base = dict(is_internship=False, disclosure="undisclosed", minimum=None, maximum=None, currency=None, period=None)
    base.update(kw)
    return SalaryFacts(**base)


# ── Salary policy ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "facts,expected",
    [
        (sf(), "eligible"),
        (sf(disclosure="verified", minimum=900_000, maximum=1_200_000, currency="INR", period="year"), "eligible"),
        (sf(disclosure="verified", minimum=500_000, maximum=700_000, currency="INR", period="year"), "ineligible"),
        (sf(disclosure="verified", minimum=600_000, maximum=1_000_000, currency="INR", period="year"), "manual_review"),
        (
            sf(disclosure="advertised", minimum=1_000_000, maximum=1_200_000, currency="INR", period="year"),
            "manual_review",
        ),
        (sf(disclosure="verified", minimum=100_000, maximum=150_000, currency="USD", period="year"), "manual_review"),
        (sf(disclosure="verified", minimum=70_000, maximum=90_000, currency="INR", period="month"), "eligible"),
    ],
)
def test_fulltime_salary_policy(facts, expected):
    assert evaluate(facts, POLICY).decision == expected


def test_threshold_is_configurable():
    facts = sf(disclosure="verified", minimum=900_000, maximum=1_000_000, currency="INR", period="year")
    assert evaluate(facts, SalaryPolicy(min_ctc_lpa=12)).decision == "ineligible"


def test_internship_rules_are_separate():
    # A ₹20k/month stipend is far below the full-time CTC threshold but must not be judged by it.
    facts = sf(
        is_internship=True, disclosure="verified", minimum=20_000, maximum=20_000, currency="INR", period="month"
    )
    assert evaluate(facts, POLICY).decision == "eligible"
    strict = SalaryPolicy(min_stipend_monthly_inr=25_000)
    assert evaluate(facts, strict).decision == "ineligible"
    assert evaluate(sf(is_internship=True), SalaryPolicy(allow_undisclosed_stipend=False)).decision == "manual_review"


# ── State machine ─────────────────────────────────────────────────────────────────────────────
def test_state_machine_blocks_skipping_approval():
    with pytest.raises(InvalidTransition):
        check_transition("preparing", "submitted")
    with pytest.raises(InvalidTransition):
        check_transition("awaiting_approval", "approved")  # guarded: only via approval decision
    check_transition("awaiting_approval", "approved", guarded_ok=True)
    with pytest.raises(InvalidTransition):
        check_transition("offer", "interview")


# ── Job parsing ───────────────────────────────────────────────────────────────────────────────
def test_experience_extraction():
    assert jp.experience_range("We need 3-5 years of experience in Python") == (3, 5)
    assert jp.experience_range("Minimum 2+ years of professional experience") == (2, None)
    assert jp.experience_range("Freshers welcome! Entry-level role") == (0, 1)
    assert jp.experience_range("Join our team") == (None, None)


def test_salary_extraction():
    s = jp.parse_salary_text("CTC: 12 - 18 LPA")
    assert (s.minimum, s.maximum, s.currency) == (1_200_000, 1_800_000, "INR")
    s = jp.parse_salary_text("The base salary range is $150,000 - $190,000 per year")
    assert (s.minimum, s.maximum, s.currency, s.period) == (150_000, 190_000, "USD", "year")
    s = jp.parse_salary_text("$211.4K - $290.6K")
    assert s.minimum == pytest.approx(211_400)
    assert jp.parse_salary_text("Competitive pay").minimum is None


def test_skill_split_required_vs_preferred():
    text = "Requirements\n• 3+ years with Python and PostgreSQL\n• Experience with AWS\nNice to have\n• Kubernetes, Go"
    split = jp.split_skills(text)
    assert set(split.required) == {"Python", "PostgreSQL", "AWS"}
    assert set(split.preferred) == {"Kubernetes", "Go"}


def test_html_is_sanitised():
    safe, text = jp.clean_html('&lt;p&gt;Hi&lt;/p&gt;<script>alert(1)</script><a href="javascript:x">l</a>')
    assert "<script" not in safe and "javascript:" not in safe
    assert "Hi" in text


def test_fingerprint_is_source_independent():
    a = jp.fingerprint("Stripe, Inc.", "Senior Software Engineer (Payments)", "Bengaluru, India")
    b = jp.fingerprint("stripe", "Sr. Software Engineer", "Bengaluru")
    assert a == b


# ── Taxonomy ──────────────────────────────────────────────────────────────────────────────────
def test_taxonomy_aliases_and_ambiguity():
    assert taxonomy.canonicalize("k8s") == "Kubernetes"
    assert taxonomy.canonicalize("ReactJS") == "React"
    found = taxonomy.extract_skills("Built services in Go and React Native; we go to market fast. R&D team.")
    assert "Go" in found and "React Native" in found
    assert "React" not in found  # consumed by the longer "React Native"
    assert "R" not in found
    assert taxonomy.related("PostgreSQL", "MySQL")


# ── Matching ──────────────────────────────────────────────────────────────────────────────────
def _cand(**kw):
    base = dict(
        skills={"Python": "verified", "FastAPI": "verified", "PostgreSQL": "verified", "Docker": "resume"},
        years_experience=3,
        target_titles=["Backend Engineer"],
        past_titles=[],
        preferred_locations=["Bengaluru"],
        work_modes=["remote", "hybrid"],
        experience_levels=["2-3", "3-5"],
        employment_types=["full_time"],
    )
    base.update(kw)
    return CandidateProfile(**base)


def _job(**kw):
    base = dict(
        title="Backend Engineer",
        required_skills=["Python", "MySQL", "AWS"],
        preferred_skills=["Docker"],
        experience_min=2,
        experience_max=5,
        location="Bengaluru, India",
        work_mode="hybrid",
        seniority="mid",
        employment_type="full_time",
        is_internship=False,
        salary=sf(),
    )
    base.update(kw)
    return JobFacts(**base)


def test_match_is_explainable_and_reproducible():
    r1 = compute_match(_job(), _cand(), POLICY)
    r2 = compute_match(_job(), _cand(), POLICY)
    assert r1.score == r2.score and 0 <= r1.score <= 100
    assert "Python" in r1.matched_skills
    assert {"required": "MySQL", "have": "PostgreSQL"} in r1.related_skills
    assert r1.missing_required == ["AWS"]
    assert r1.eligibility["status"] == "eligible"
    assert any("Not yet verified" in c for c in r1.concerns)  # Docker only from resume


def test_unknown_factors_are_excluded_not_zeroed():
    r = compute_match(_job(experience_min=None, experience_max=None), _cand(), POLICY)
    assert "experience" in r.unknown_factors
    assert r.eligibility["confidence"] < 1


def test_experience_gap_and_salary_block():
    r = compute_match(_job(experience_min=8, experience_max=None), _cand(), POLICY)
    assert r.eligibility["status"] == "ineligible"
    low_pay = sf(disclosure="verified", minimum=300_000, maximum=400_000, currency="INR", period="year")
    r = compute_match(_job(salary=low_pay), _cand(), POLICY)
    assert r.eligibility["status"] == "ineligible"
    assert r.eligibility["salary"]["decision"] == "ineligible"


def test_better_fit_scores_higher():
    good = compute_match(_job(required_skills=["Python", "PostgreSQL"]), _cand(), POLICY).score
    poor = compute_match(_job(title="Sales Manager", required_skills=["Salesforce", "CRM"]), _cand(), POLICY).score
    assert good > poor + 30


def test_low_information_jobs_are_shrunk_toward_neutral():
    sparse = compute_match(
        _job(required_skills=[], preferred_skills=[], experience_min=None, experience_max=None, seniority=None),
        _cand(),
        POLICY,
    )
    full = compute_match(_job(required_skills=["Python", "PostgreSQL"]), _cand(), POLICY)
    assert sparse.eligibility["confidence"] < 0.5
    assert sparse.eligibility["known_factor_score"] >= 90  # what remains known looks perfect…
    assert sparse.score < 75  # …but the score reflects how little is known
    assert full.score > sparse.score
