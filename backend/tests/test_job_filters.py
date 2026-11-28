"""Phase 3 M4: discovery filters. Search and discovery automations share one set of listing rules; a fact the
listing doesn't state never hides it; filters never change an eligibility decision."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import utcnow
from app.domain import job_filters as jf
from app.domain.job_parsing import EMPLOYMENT_TYPES
from app.integrations.sources import providers
from app.integrations.sources.base import FetchResult
from app.models import Application, ApprovalRequest, AutomationConfig, Job, JobMatch, User
from app.services import automation as auto
from tests.helpers import job
from tests.test_workflows import _onboard

V = "/api/v1"

# One listing per employment type, plus one that doesn't state its type (or work mode, or experience).
LISTINGS = {
    "Backend Engineer": (
        "Full-time role. Hybrid, 3 days in office.\nRequirements\n• Python\n• 3+ years of experience",
        {},
    ),
    "Platform Engineer": ("Requirements\n• Python", {}),
    "Data Analyst": ("This is a part-time role, fully remote. Requirements: Python. 1-2 years of experience.", {}),
    "Backend Developer": ("6-month contract. Requirements: Python. 8+ years of experience.", {}),
    "Data Intern": ("Summer internship. Requirements: SQL", {}),
    "Writer": ("This is a freelance engagement. Python", {}),
    "Support Engineer (Temporary)": ("Temporary cover for 4 months. Python. On-site role.", {}),
    "Software Apprentice": ("Our apprenticeship pays a stipend. Python", {}),
    # Stored as full-time by the board but flagged as an internship: an internship everywhere.
    "Engineering Intern": ("Python", {"employment_type": "full_time", "is_internship": True}),
}
TYPE_OF = {
    "Backend Engineer": "full_time",
    "Data Analyst": "part_time",
    "Backend Developer": "contract",
    "Data Intern": "internship",
    "Engineering Intern": "internship",
    "Writer": "freelance",
    "Support Engineer (Temporary)": "temporary",
    "Software Apprentice": "apprenticeship",
}
UNSTATED = {"Platform Engineer"}


class Board:
    provider = "greenhouse"

    def __init__(self, listings=None):
        self.listings = LISTINGS if listings is None else listings
        self.extra = []

    def fetch(self, identifier):
        jobs = [job(str(i), t, text, **kw) for i, (t, (text, kw)) in enumerate(self.listings.items())]
        jobs += self.extra
        return FetchResult("Acme Payments", jobs, raw_count=len(jobs))


@pytest.fixture
def board(monkeypatch):
    b = Board()
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", b)
    return b


def _follow(api):
    r = api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    assert r.status_code == 201, r.text


def _titles(api, **params):
    r = api.get(f"{V}/jobs", params={"page_size": 100, **params})
    assert r.status_code == 200, r.text
    return {i["title"] for i in r.json()["items"]}


# ── Employment type ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", EMPLOYMENT_TYPES)
def test_each_of_the_seven_types_filters_and_keeps_unstated_listings(user_api, board, kind):
    _onboard(user_api)
    _follow(user_api)
    want = {t for t, k in TYPE_OF.items() if k == kind} | UNSTATED
    assert _titles(user_api, employment_types=kind) == want


def test_multiple_types_are_any_of_and_empty_means_every_job(user_api, board):
    _onboard(user_api)
    _follow(user_api)
    everything = _titles(user_api)
    assert everything == set(LISTINGS)
    assert _titles(user_api, employment_types="") == everything
    assert (
        _titles(user_api, employment_types="freelance,temporary")
        == {"Writer", "Support Engineer (Temporary)"} | UNSTATED
    )
    assert _titles(user_api, employment_types=",".join(EMPLOYMENT_TYPES)) == everything
    # The original three values (older clients) behave as before for listings that state a type.
    assert (
        _titles(user_api, employment_types="full_time,internship,contract")
        == {"Backend Engineer", "Backend Developer", "Data Intern", "Engineering Intern"} | UNSTATED
    )


def test_unstated_type_is_returned_as_unknown_not_classified(user_api, board):
    _onboard(user_api)
    _follow(user_api)
    items = user_api.get(f"{V}/jobs", params={"employment_types": "full_time", "page_size": 100}).json()["items"]
    unstated = next(i for i in items if i["title"] == "Platform Engineer")
    assert unstated["employment_type"] is None and unstated["is_internship"] is False


def test_internship_flag_wins_over_the_stored_type_in_search_and_automation(user_api, board, db):
    _onboard(user_api)
    _follow(user_api)
    assert "Engineering Intern" not in _titles(user_api, employment_types="full_time")
    assert "Engineering Intern" in _titles(user_api, employment_types="internship")
    j = db.scalar(select(Job).where(Job.title == "Engineering Intern"))
    assert j.employment_type == "full_time" and j.is_internship
    assert jf.employment_kind(j) == "internship"
    assert not jf.employment_ok(j, ["full_time"]) and jf.employment_ok(j, ["internship"])


@pytest.mark.parametrize("value", ["Full-time", "bogus", "full_time,FULL_TIME", "full time"])
def test_unknown_filter_values_are_rejected_with_the_allowed_values(user_api, board, value):
    _follow(user_api)
    r = user_api.get(f"{V}/jobs", params={"employment_types": value})
    assert r.status_code == 422
    assert "Allowed: full_time, part_time, contract, internship, freelance, temporary, apprenticeship" in r.text
    assert user_api.get(f"{V}/jobs", params={"work_modes": "office"}).status_code == 422
    assert user_api.get(f"{V}/jobs", params={"experience_levels": "10+"}).status_code == 422


# ── Work mode, experience, posting date ─────────────────────────────────────────────────────────
def test_unstated_work_mode_and_experience_stay_visible(user_api, board):
    _onboard(user_api)
    _follow(user_api)
    remote = _titles(user_api, work_modes="remote")
    assert "Data Analyst" in remote and "Platform Engineer" in remote  # remote · not stated
    assert "Backend Engineer" not in remote and "Support Engineer (Temporary)" not in remote  # hybrid · on-site
    mid = _titles(user_api, experience_levels="3-5")
    assert "Backend Engineer" in mid and "Platform Engineer" in mid  # 3+ · not stated
    assert "Backend Developer" not in mid and "Data Analyst" not in mid  # 8+ · 1–2
    assert "Data Intern" in _titles(user_api, experience_levels="internship")


def test_posted_within_keeps_listings_without_a_date(user_api, board):
    old, new = (
        job("90", "Old Role", "Python", posted_at=utcnow() - timedelta(days=40)),
        job("91", "New Role", "Python", posted_at=utcnow() - timedelta(days=2)),
    )
    board.listings, board.extra = {"Undated Role": ("Python", {})}, [old, new]
    _follow(user_api)
    assert _titles(user_api, posted_within_days=7) == {"New Role", "Undated Role"}


# ── Minimum pay ───────────────────────────────────────────────────────────────────────────────
def _paid(ext, title, lo, hi, currency, period, disclosure="verified"):
    n = job(ext, title, "Python")
    n.salary_min, n.salary_max, n.salary_currency, n.salary_period, n.salary_disclosure = (
        lo,
        hi,
        currency,
        period,
        disclosure,
    )
    return n


def test_min_pay_compares_annual_inr_and_keeps_pay_it_cannot_compare(user_api, board):
    board.listings = {"Undisclosed": ("Python", {})}
    board.extra = [
        _paid("1", "Monthly 70-90k", 70_000, 90_000, "INR", "month"),  # ₹8.4–10.8 LPA
        _paid("2", "Monthly 50k", 50_000, 50_000, "INR", "month"),  # ₹6 LPA
        _paid("3", "Yearly 12L", 1_000_000, 1_200_000, "INR", "year"),
        _paid("4", "Yearly 6L", 500_000, 600_000, "INR", "year"),
        _paid("5", "Hourly", 500, 800, "INR", "hour"),
        _paid("6", "No period", 50_000, 50_000, "INR", None),
        _paid("7", "No currency", 50_000, 50_000, None, "year"),
        _paid("8", "Dollar sign", 50_000, 50_000, "$", "year"),
        _paid("9", "USD", 50_000, 60_000, "USD", "year"),
    ]
    _follow(user_api)
    assert _titles(user_api, min_salary_lpa=8) == {
        "Undisclosed",
        "Monthly 70-90k",
        "Yearly 12L",
        "Hourly",
        "No period",
        "No currency",
        "Dollar sign",
        "USD",
    }
    assert "Yearly 12L" in _titles(user_api, min_salary_lpa=12) and "Monthly 70-90k" not in _titles(
        user_api, min_salary_lpa=12
    )


def test_annual_inr_never_guesses():
    class L:
        salary_max, salary_currency, salary_period = 90_000, "INR", "month"

    assert jf.annual_inr_max(L) == 1_080_000
    for currency, period in ((None, "month"), ("$", "month"), ("USD", "year"), ("INR", "hour"), ("INR", None)):
        L.salary_currency, L.salary_period = currency, period
        assert jf.annual_inr_max(L) is None and jf.pay_ok(L, 100)


# ── Eligibility, pagination, history ──────────────────────────────────────────────────────────
def test_filters_never_change_or_relabel_eligibility(user_api, board, db):
    _onboard(user_api)  # prefers full-time only
    _follow(user_api)
    every = {i["title"]: i for i in user_api.get(f"{V}/jobs", params={"page_size": 100}).json()["items"]}
    assert every["Writer"]["eligibility"] == "ineligible"  # freelance isn't a type the user selected
    before = {m.job_id: (m.eligibility, m.computed_at) for m in db.scalars(select(JobMatch))}
    filtered = user_api.get(f"{V}/jobs", params={"employment_types": "freelance", "page_size": 100}).json()["items"]
    assert {i["title"]: i["eligibility"] for i in filtered} == {
        t: every[t]["eligibility"] for t in {"Writer"} | UNSTATED
    }
    eligible_only = user_api.get(
        f"{V}/jobs", params={"employment_types": "freelance,full_time", "eligibility": "eligible", "page_size": 100}
    ).json()["items"]
    assert all(i["eligibility"] == "eligible" for i in eligible_only) and "Writer" not in {
        i["title"] for i in eligible_only
    }
    db.expire_all()
    assert {m.job_id: (m.eligibility, m.computed_at) for m in db.scalars(select(JobMatch))} == before


def test_pages_are_stable_and_complete_with_filters(user_api, board):
    _onboard(user_api)
    _follow(user_api)
    params = {"employment_types": "full_time,internship,apprenticeship,freelance", "sort": "recent"}
    total = user_api.get(f"{V}/jobs", params=params).json()["total"]
    for _ in range(2):  # the same pages on every request
        seen = []
        for page in range(1, total + 1):
            r = user_api.get(f"{V}/jobs", params={**params, "page": page, "page_size": 1}).json()
            assert r["total"] == total
            seen += [i["id"] for i in r["items"]]
        if _ == 0:
            first = seen
    assert seen == first and len(set(seen)) == total == len(_titles(user_api, **params))


def test_saved_decision_is_not_recomputed_by_filtering(user_api, board):
    _onboard(user_api)
    _follow(user_api)
    jid = next(i["id"] for i in user_api.get(f"{V}/jobs").json()["items"] if i["title"] == "Backend Engineer")
    user_api.put(f"{V}/jobs/{jid}/state", json={"state": "saved"})
    at = user_api.get(f"{V}/jobs/{jid}").json()["eligibility_detail"]["evaluated_at"]
    for p in ({"employment_types": "full_time"}, {"work_modes": "hybrid"}, {"min_salary_lpa": 50}, {"state": "saved"}):
        user_api.get(f"{V}/jobs", params=p)
    assert user_api.get(f"{V}/jobs/{jid}").json()["eligibility_detail"]["evaluated_at"] == at


# ── Automation: saving ────────────────────────────────────────────────────────────────────────
def _create(api, **filters):
    return api.post(
        f"{V}/automations", json={"name": "D", "kind": "discovery", "interval_minutes": 60, "filters": filters}
    )


def test_automation_accepts_all_seven_types_and_validates_values(user_api):
    r = _create(user_api, employment_types=list(EMPLOYMENT_TYPES), experience_levels=["3-5"], work_modes=["remote"])
    assert r.status_code == 201 and r.json()["filters"]["employment_types"] == list(EMPLOYMENT_TYPES)
    assert r.json()["filters"]["experience_levels"] == ["3-5"]
    bad = _create(user_api, employment_types=["full_time", "Full-time"])
    assert bad.status_code == 400 and "Unknown employment_types: Full-time" in bad.text
    assert _create(user_api, work_modes=["office"]).status_code == 400
    assert _create(user_api, experience_levels=["10+"]).status_code == 400
    assert _create(user_api, employment_types=[1]).status_code == 400
    for v in ("abc", True, 1.5, None):
        assert _create(user_api, min_score=v).status_code == 400
    assert _create(user_api, min_score=101).status_code == 400
    assert _create(user_api, min_score=50.0).json()["filters"]["min_score"] == 50
    assert _create(user_api, locations="Bengaluru").json()["filters"]["locations"] == ["Bengaluru"]
    assert _create(user_api, title_keywords=[1]).status_code == 400


def test_older_clients_and_single_values(user_api):
    # An older client that sends only the original fields, or a single string, still works.
    r = _create(user_api, min_score=60, notify_min_score=70, max_shortlist=3, employment_types="full_time")
    assert r.status_code == 201
    f = r.json()["filters"]
    assert f["employment_types"] == ["full_time"] and f["experience_levels"] == [] and f["work_modes"] == []
    assert _create(user_api).json()["filters"] == auto.DEFAULT_FILTERS["discovery"]


def test_editing_an_automation_keeps_its_other_settings(user_api):
    cfg = _create(
        user_api, min_score=55, locations=["Pune"], title_keywords=["backend"], employment_types=["contract"]
    ).json()
    body = {
        "name": "Renamed",
        "kind": "discovery",
        "interval_minutes": 180,
        "filters": {**cfg["filters"], "employment_types": ["apprenticeship", "contract"]},
    }
    out = user_api.put(f"{V}/automations/{cfg['id']}", json=body).json()
    assert out["filters"] == {**cfg["filters"], "employment_types": ["apprenticeship", "contract"]}
    assert out["name"] == "Renamed" and out["interval_minutes"] == 180


# ── Automation: running ───────────────────────────────────────────────────────────────────────
def _no_restriction(api):
    prof = api.get(f"{V}/profile").json()["profile"]
    prof["employment_types"] = []  # no type preference: eligibility doesn't reject on type
    assert api.put(f"{V}/profile", json=prof).status_code == 200


def _run(db, api, filters):
    """A discovery run over the board's listings, which are all new to this run."""
    user = db.scalar(select(User))
    cfg = AutomationConfig(
        user_id=user.id,
        name="D",
        kind="discovery",
        interval_minutes=60,
        state="active",
        filters={"min_score": 0, "notify_min_score": 0, "max_shortlist": 25, **filters},
    )
    db.add(cfg)
    db.commit()
    _follow(api)
    ex = auto.execute(db, user, "discovery", cfg, "manual")
    shortlisted = {a.job.title for a in db.scalars(select(Application).where(Application.user_id == user.id))}
    return ex, shortlisted


def test_automation_uses_the_same_rules_as_search(user_api, board, db):
    _onboard(user_api)
    _no_restriction(user_api)
    ex, shortlisted = _run(db, user_api, {"employment_types": ["apprenticeship", "freelance"]})
    assert ex.status == "succeeded"
    assert shortlisted == {"Software Apprentice", "Writer"} | UNSTATED  # an unstated type is included
    assert shortlisted == _titles(user_api, employment_types="apprenticeship,freelance") - {
        t for t, i in _cards(user_api).items() if i["eligibility"] == "ineligible"
    }


def _cards(api):
    return {i["title"]: i for i in api.get(f"{V}/jobs", params={"page_size": 100}).json()["items"]}


def test_automation_applies_experience_levels(user_api, board, db):
    _onboard(user_api)
    _no_restriction(user_api)
    _, shortlisted = _run(db, user_api, {"experience_levels": ["5+"]})
    assert "Backend Engineer" in shortlisted  # 3+ overlaps 5+
    assert "Data Analyst" not in shortlisted  # 1–2
    assert "Platform Engineer" in shortlisted  # not stated
    rejected = {t for t, i in _cards(user_api).items() if i["eligibility"] == "ineligible"}
    assert shortlisted == _titles(user_api, experience_levels="5+") - rejected  # same rule as search


def test_automation_never_shortlists_rejected_jobs_and_never_submits(user_api, board, db):
    _onboard(user_api)  # prefers full-time: other stated types are rejected
    _, shortlisted = _run(db, user_api, {"employment_types": list(EMPLOYMENT_TYPES)})
    rejected = {t for t, i in _cards(user_api).items() if i["eligibility"] == "ineligible"}
    assert rejected and not (shortlisted & rejected)
    statuses = {a.status for a in db.scalars(select(Application))}
    assert statuses <= {"shortlisted"}
    assert db.scalar(select(ApprovalRequest)) is None  # discovery never asks for approval, let alone submits


@pytest.mark.parametrize(
    ("saved", "expect_shortlist", "warned"),
    [
        ("apprenticeship", {"Software Apprentice"} | UNSTATED, False),  # a single string (older/hand-made)
        (["apprenticeship", "Apprentice"], {"Software Apprentice"} | UNSTATED, True),  # unknown value ignored
        (["bogus"], set(), True),  # nothing recognised: matches no job, as before; never every job
        ({"x": 1}, set(), True),
    ],
)
def test_saved_configurations_with_old_values_never_crash(user_api, board, db, saved, expect_shortlist, warned):
    _onboard(user_api)
    _no_restriction(user_api)
    ex, shortlisted = _run(db, user_api, {"employment_types": saved})
    assert ex.status == "succeeded"
    assert shortlisted == expect_shortlist
    assert any(line["event"] == "filter_values_ignored" for line in ex.log) is warned


def test_choices_helpers():
    assert jf.choices("employment_types", None) == []
    assert jf.choices("employment_types", "contract") == ["contract"]
    assert jf.choices("employment_types", ["contract", "contract", "temporary"]) == ["contract", "temporary"]
    with pytest.raises(jf.InvalidFilter):
        jf.choices("employment_types", ["Contract"])
    assert jf.stored_choices("work_modes", ["remote", 3, "x"]) == (["remote"], ["3", "x"])
    assert jf.CHOICES["employment_types"] == EMPLOYMENT_TYPES  # one taxonomy, not a copy
