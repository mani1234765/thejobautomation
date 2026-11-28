"""Regression tests for the Phase 2 review fixes: atomic daily limit, tolerant settings, city aliases,
skills rejection only on explicit mandatory evidence, review when a preferred fact is missing."""

import threading
import time

import pytest
from sqlalchemy import select

from app.db import session_factory
from app.domain.locations import canonical_city, cities_in, mentions_city
from app.domain.matching import CandidateProfile, compute_match
from app.domain.salary_policy import MAX_DAILY_APPLICATIONS, SalaryPolicy
from app.integrations.sources import providers
from app.models import ApplicationEvent, User, UserSettings
from app.services import applications as app_svc
from app.services import profile as profile_svc
from tests.conftest import TEST_DATABASE_URL, make_client
from tests.helpers import FakeAdapter, job
from tests.test_eligibility_policy import CAND, check, facts
from tests.test_workflows import _onboard

V = "/api/v1"
LOGIN = {"email": "asha@example.com", "password": "correct-horse-42"}


def verdict(job_facts, cand=CAND):
    return compute_match(job_facts, cand, SalaryPolicy()).eligibility


def cand(**kw):
    return CandidateProfile(**{**CAND.__dict__, **kw})


# ── 1. Daily limit under genuinely simultaneous requests (PostgreSQL) ───────────────────────────
postgres_only = pytest.mark.skipif(
    not (TEST_DATABASE_URL or "").startswith("postgresql"), reason="needs PostgreSQL row locks"
)


def _approved_applications(api, n: int) -> list[str]:
    ids = []
    for jid in [j["id"] for j in api.get(f"{V}/jobs", params={"page_size": 50}).json()["items"]][:n]:
        aid = api.post(f"{V}/applications", json={"job_id": jid}).json()["id"]
        detail = api.post(f"{V}/applications/{aid}/prepare", json={}).json()["application"]
        for a in detail["answers"]:
            if a["needs_review"]:
                api.put(f"{V}/applications/{aid}/answers/{a['id']}", json={"answer": "n/a"})
        ap = api.post(f"{V}/applications/{aid}/request-approval").json()
        assert api.post(f"{V}/approvals/{ap['id']}/decide", json={"approve": True}).status_code == 200
        ids.append(aid)
    return ids


def _setup(user_api, monkeypatch, n_apps: int, limit: int) -> tuple[list[str], str]:
    jobs = [
        job(str(i), f"Backend Engineer {i}", "Python and PostgreSQL. 2-4 years of experience") for i in range(n_apps)
    ]
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", FakeAdapter(jobs))
    _onboard(user_api)
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    s = user_api.get(f"{V}/settings").json()
    assert user_api.put(f"{V}/settings", json={**s, "daily_application_limit": limit}).status_code == 200
    return _approved_applications(user_api, n_apps), user_api.get(f"{V}/auth/me").json()["id"]


def _fire_simultaneously(app_ids: list[str]) -> list[int]:
    """One fresh signed-in client per thread; a barrier releases every request at the same instant."""
    clients = [make_client() for _ in app_ids]
    for c in clients:
        assert c.post(f"{V}/auth/login", json=LOGIN).status_code == 200
    barrier = threading.Barrier(len(app_ids))
    statuses: list[int] = []
    lock = threading.Lock()

    def go(c, aid):
        barrier.wait()
        r = c.post(f"{V}/applications/{aid}/submission", json={"action": "start"})
        with lock:
            statuses.append(r.status_code)

    threads = [threading.Thread(target=go, args=(c, a)) for c, a in zip(clients, app_ids, strict=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return statuses


def _starts(user_id: str) -> int:
    with session_factory()() as db:
        return app_svc.submissions_last_24h(db, user_id)


def _widen_race_window(monkeypatch):
    """Make the count slow so overlapping requests are guaranteed to interleave (logic unchanged)."""
    real = app_svc.submissions_last_24h

    def slow(db, uid):
        n = real(db, uid)
        time.sleep(0.3)
        return n

    monkeypatch.setattr(app_svc, "submissions_last_24h", slow)


@postgres_only
def test_simultaneous_starts_never_exceed_the_limit(user_api, monkeypatch):
    ids, uid = _setup(user_api, monkeypatch, n_apps=6, limit=2)
    _widen_race_window(monkeypatch)
    statuses = _fire_simultaneously(ids)
    assert sorted(statuses) == [200, 200, 409, 409, 409, 409], statuses
    assert _starts(uid) == 2


@postgres_only
def test_simultaneous_starts_without_widening_also_respect_the_limit(user_api, monkeypatch):
    ids, uid = _setup(user_api, monkeypatch, n_apps=8, limit=3)
    statuses = _fire_simultaneously(ids)
    assert statuses.count(200) == 3 and statuses.count(409) == 5, statuses
    assert _starts(uid) == 3


@postgres_only
def test_the_concurrency_test_detects_the_race_when_the_lock_is_removed(user_api, monkeypatch):
    """Control: with the row lock disabled, the same simultaneous requests exceed the limit. This shows the
    tests above would fail if the atomic enforcement regressed."""
    ids, uid = _setup(user_api, monkeypatch, n_apps=4, limit=1)
    monkeypatch.setattr(app_svc, "lock_submission_quota", lambda db, user: None)
    _widen_race_window(monkeypatch)
    _fire_simultaneously(ids)
    assert _starts(uid) > 1


@postgres_only
def test_simultaneous_double_start_of_one_application_records_one_attempt(user_api, monkeypatch):
    ids, uid = _setup(user_api, monkeypatch, n_apps=1, limit=5)
    _widen_race_window(monkeypatch)
    statuses = _fire_simultaneously([ids[0]] * 4)
    assert statuses == [200] * 4  # idempotent for the same application…
    with session_factory()() as db:  # …and exactly one start event
        events = db.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id == ids[0])).all()
        starts = [e for e in events if e.kind == "submission" and e.detail.get("action") == "start"]
    assert len(starts) == 1 and _starts(uid) == 1


# ── 2. GET /settings tolerates invalid stored values ───────────────────────────────────────────
def _store(**values):
    with session_factory()() as db:
        s = db.scalar(select(UserSettings))
        for k, v in values.items():
            setattr(s, k, v)
        db.commit()


def _stored(field):
    with session_factory()() as db:
        return getattr(db.scalar(select(UserSettings)), field)


@pytest.mark.parametrize(
    ("stored", "effective"), [(20_000, MAX_DAILY_APPLICATIONS), (10_000_000, MAX_DAILY_APPLICATIONS), (0, 1), (-3, 1)]
)
def test_invalid_stored_limit_is_normalised_and_reported(user_api, stored, effective):
    _store(daily_application_limit=stored)
    r = user_api.get(f"{V}/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["daily_application_limit"] == effective
    [n] = body["normalized"]
    assert n["field"] == "daily_application_limit" and n["stored"] == stored and n["effective"] == effective
    assert _stored("daily_application_limit") == stored  # GET never rewrites the stored value
    with session_factory()() as db:
        assert app_svc.daily_limit(db.scalar(select(User))) == effective  # enforcement uses the same value


def test_invalid_stored_threshold_and_approval_flag_are_normalised(user_api):
    _store(min_ctc_lpa=350.0, require_approval=False, min_match_score=140)
    body = user_api.get(f"{V}/settings").json()
    assert body["min_ctc_lpa"] == 200.0 and body["require_approval"] is True and body["min_match_score"] == 100
    assert {n["field"] for n in body["normalized"]} == {"min_ctc_lpa", "require_approval", "min_match_score"}
    with session_factory()() as db:
        assert profile_svc.salary_policy(db.scalar(select(User))).min_ctc_lpa == 200.0


def test_saving_settings_repairs_and_clears_the_report(user_api):
    _store(daily_application_limit=20_000)
    body = user_api.get(f"{V}/settings").json()
    assert body["normalized"]
    saved = user_api.put(f"{V}/settings", json=body)  # the UI sends back the effective values
    assert saved.status_code == 200 and saved.json()["normalized"] == []
    assert _stored("daily_application_limit") == MAX_DAILY_APPLICATIONS
    assert user_api.get(f"{V}/settings").json()["normalized"] == []


def test_valid_stored_values_are_untouched(user_api):
    _store(daily_application_limit=10, min_ctc_lpa=12.5)  # e.g. an existing user's saved values
    body = user_api.get(f"{V}/settings").json()
    assert body["daily_application_limit"] == 10 and body["min_ctc_lpa"] == 12.5 and body["normalized"] == []


# ── 3. City aliases and work arrangements ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "canon"),
    [
        ("Bangalore", "bengaluru"),
        ("Bengaluru, India", "bengaluru"),
        ("BLR", "bengaluru"),
        ("Bombay", "mumbai"),
        ("Mumbai, Maharashtra", "mumbai"),
        ("Gurgaon", "gurugram"),
        ("Madras", "chennai"),
        ("Coimbatore", "coimbatore"),
    ],
)
def test_canonical_city(raw, canon):
    assert canonical_city(raw) == canon


def test_city_mentions_use_whole_words_and_aliases():
    assert mentions_city("Bangalore, Karnataka", "Bengaluru")
    assert mentions_city("Bengaluru / Hyderabad", "Bangalore")
    assert mentions_city("Bombay office", "Mumbai") and mentions_city("Mumbai, India", "Bombay")
    assert not mentions_city("Puneet Nagar, Delhi", "Pune")  # substring, not a city match
    assert mentions_city("Pune, India", "Poona")
    assert cities_in("Remote - India") == set()


@pytest.mark.parametrize(
    ("location", "mode", "pref_cities", "modes", "outcome", "code"),
    [
        ("Bangalore, India", "hybrid", ["Bengaluru"], ["hybrid"], "pass", "LOCATION_CITY_MATCH"),
        ("Bengaluru, India", "onsite", ["Bangalore"], ["onsite"], "pass", "LOCATION_CITY_MATCH"),
        ("Bombay", "onsite", ["Mumbai"], ["onsite", "hybrid"], "pass", "LOCATION_CITY_MATCH"),
        ("Mumbai, India", "hybrid", ["Bombay"], ["hybrid"], "pass", "LOCATION_CITY_MATCH"),
        ("Bangalore, India", "hybrid", ["Bengaluru"], ["remote"], "reject", "LOCATION_MODE_MISMATCH"),
        ("Chennai, India", "hybrid", ["Bengaluru"], ["hybrid"], "reject", "LOCATION_CITY_MISMATCH"),
        ("Remote - India", "remote", ["Bengaluru"], ["remote", "hybrid"], "pass", "LOCATION_REMOTE_OK"),
        ("Remote - India", "remote", ["Bengaluru"], ["onsite"], "reject", "LOCATION_MODE_MISMATCH"),
        ("India", "onsite", ["Bengaluru"], ["onsite"], "review", "LOCATION_CITY_NOT_STATED"),
        ("Bangalore", None, ["Bengaluru"], ["hybrid"], "review", "LOCATION_MODE_NOT_STATED"),
        ("Bangalore", None, ["Bengaluru"], ["remote", "hybrid", "onsite"], "pass", "LOCATION_CITY_MATCH"),
        (None, "hybrid", ["Bengaluru"], ["hybrid"], "review", "LOCATION_CITY_NOT_STATED"),
        (None, None, [], ["hybrid"], "review", "LOCATION_NOT_STATED"),
        (None, None, [], [], "pass", "LOCATION_NO_PREFERENCE"),
    ],
)
def test_location_aliases_and_work_arrangements(location, mode, pref_cities, modes, outcome, code):
    e = verdict(
        facts(location=location, work_mode=mode, description="Join our team."),
        cand(preferred_locations=pref_cities, work_modes=modes),
    )
    c = check(e, "LOCATION")
    assert (c["outcome"], c["code"]) == (outcome, code), c


def test_match_score_location_factor_uses_aliases():
    r = compute_match(
        facts(location="Bangalore, India", work_mode="hybrid"), cand(preferred_locations=["Bengaluru"]), SalaryPolicy()
    )
    assert next(f for f in r.factors if f.key == "location").score == 1.0


# ── 4. Skills: rejection only on explicit mandatory evidence ──────────────────────────────────
def test_zero_extracted_matches_is_review_not_rejection():
    e = verdict(facts(required_skills=["Security", "Git", "Communication"], description="We value Security and Git."))
    assert check(e, "SKILLS")["code"] == "SKILLS_NONE_MATCHED" and check(e, "SKILLS")["outcome"] == "review"
    assert e["verdict"] == "MANUAL_REVIEW"


def test_explicitly_mandatory_skills_all_missing_is_rejected_with_evidence():
    text = "Requirements\n• Must have: Java and Kotlin.\n• Spring Boot is required."
    e = verdict(facts(required_skills=["Java", "Kotlin", "Spring"], description=text))
    c = check(e, "SKILLS")
    assert e["verdict"] == "REJECTED" and c["code"] == "SKILLS_MANDATORY_MISSING"
    assert c["evidence"] == ["“• Must have: Java and Kotlin.”", "“• Spring Boot is required.”"]


def test_one_mandatory_skill_is_not_enough_to_reject():
    e = verdict(facts(required_skills=["Java", "Kotlin"], description="Must have Java. Kotlin is a plus."))
    assert check(e, "SKILLS")["outcome"] == "review"


def test_mandatory_skills_partly_missing_is_review():
    text = "Must have: Python and Go."
    e = verdict(facts(required_skills=["Python", "Go"], description=text))
    c = check(e, "SKILLS")
    assert c["code"] == "SKILLS_MANDATORY_PARTLY_MISSING" and c["outcome"] == "review" and "Go" in c["explanation"]


def test_related_skill_prevents_mandatory_rejection():
    # PostgreSQL is related to MySQL in the taxonomy; having a related skill is not "none of them".
    text = "Must have: MySQL and Redis."
    e = verdict(facts(required_skills=["MySQL", "Redis"], description=text))
    assert check(e, "SKILLS")["outcome"] != "reject"


# ── 5. Missing employment type with a preference ────────────────────────────────────────────
def test_missing_employment_type_needs_review_only_when_restricted():
    e = verdict(facts(employment_type=None))
    assert check(e, "EMPLOYMENT")["outcome"] == "review" and e["verdict"] == "MANUAL_REVIEW"
    e = verdict(facts(employment_type=None), cand(employment_types=[]))
    assert check(e, "EMPLOYMENT")["outcome"] == "unknown"
    every = ["full_time", "part_time", "contract", "internship", "freelance", "temporary", "apprenticeship"]
    e = verdict(facts(employment_type=None), cand(employment_types=every))
    assert check(e, "EMPLOYMENT")["outcome"] == "unknown"
    # M3: the four original types are a restriction now that there are seven, so a missing type is a review.
    e = verdict(facts(employment_type=None), cand(employment_types=every[:4]))
    assert check(e, "EMPLOYMENT")["outcome"] == "review"
