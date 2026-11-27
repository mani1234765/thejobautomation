"""Phase 3 M1: job-board contracts, retries, sync safety, URL validation and decision preservation.
All HTTP is mocked (respx); no network, no external writes."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.integrations.sources import base, providers
from app.integrations.sources.base import FetchResult, SourceError, safe_url
from tests.helpers import job
from tests.test_workflows import _onboard

V = "/api/v1"
FIX = Path(__file__).parent / "board_fixtures"
GH = "https://boards-api.greenhouse.io/v1/boards/example-co/jobs"
LV = "https://api.lever.co/v0/postings/example-co"
AB = "https://api.ashbyhq.com/posting-api/job-board/example-co"


def fixture(name):
    return json.loads((FIX / f"{name}.json").read_text())


@pytest.fixture
def sleeps(monkeypatch):
    """Record backoff waits instead of sleeping."""
    waits: list[float] = []
    monkeypatch.setattr(base, "_sleep", waits.append)
    return waits


# ── Contracts (sanitized fixtures) ─────────────────────────────────────────────────────────────
@respx.mock
def test_greenhouse_contract():
    respx.get(GH).mock(return_value=httpx.Response(200, json=fixture("greenhouse")))
    r = providers.GreenhouseAdapter().fetch("example-co")
    assert r.company == "Example Co" and r.raw_count == 5
    assert [j.external_id for j in r.jobs] == ["4000001", "4000002"]
    be = r.jobs[0]
    assert be.title == "Backend Engineer" and be.location == "Bengaluru, India" and be.department == "Engineering"
    assert be.url == "https://boards.greenhouse.io/example-co/jobs/4000001" and be.posted_at.year == 2026
    assert (be.experience_min, be.experience_max) == (3.0, 5.0) and be.work_mode == "hybrid"
    assert {"Python", "PostgreSQL"} <= set(be.required_skills)
    assert r.jobs[1].description_text == ""  # null content is tolerated
    reasons = {(s.external_id, s.reason) for s in r.skipped}
    assert reasons == {
        (None, "listing has no id"),
        ("4000003", "listing has no valid http(s) URL"),
        ("4000001", "duplicate id in the same response"),
    }


@respx.mock
def test_lever_contract():
    respx.get(LV).mock(return_value=httpx.Response(200, json=fixture("lever")))
    r = providers.LeverAdapter().fetch("example-co")
    assert r.raw_count == 3 and len(r.jobs) == 2 and [s.reason for s in r.skipped] == ["listing has no id"]
    pe, se = r.jobs
    assert pe.work_mode == "remote" and pe.employment_type == "full_time" and pe.department == "Infrastructure"
    assert (pe.salary_min, pe.salary_max, pe.salary_currency, pe.salary_period) == (120000, 150000, "USD", "year")
    assert pe.apply_url.endswith("/apply")
    assert se.employment_type == "contract" and se.work_mode == "onsite"
    assert se.apply_url is None  # data: URL rejected
    # Structured pay without a currency: amounts recorded, currency left unknown (never assumed INR).
    assert se.salary_disclosure == "verified" and se.salary_currency is None


@respx.mock
def test_ashby_contract():
    respx.get(AB).mock(return_value=httpx.Response(200, json=fixture("ashby")))
    r = providers.AshbyAdapter().fetch("example-co")
    assert r.raw_count == 3  # the unlisted posting isn't an open role
    assert [j.external_id for j in r.jobs] == ["a1b2c3d4-0001", "a1b2c3d4-0002"]
    assert [(s.external_id, s.reason) for s in r.skipped] == [("a1b2c3d4-0004", "listing has no title")]
    pd, intern = r.jobs
    assert pd.employment_type == "full_time" and pd.work_mode == "hybrid"
    assert (pd.salary_min, pd.salary_currency, pd.salary_disclosure) == (2_500_000, "INR", "verified")
    assert intern.is_internship and intern.work_mode == "remote" and intern.salary_currency == "$"  # ambiguous


@pytest.mark.parametrize(
    ("url", "body"),
    [
        (GH, {"meta": {}}),
        (GH, {"jobs": None}),
        (GH, ["not", "an", "object"]),
        (LV, {"ok": False}),
        (AB, {"jobs": None}),
        (AB, {"jobs": "x"}),
    ],
)
@respx.mock
def test_malformed_responses_are_source_errors(url, body):
    respx.get(url).mock(return_value=httpx.Response(200, json=body))
    adapter = {GH: providers.GreenhouseAdapter(), LV: providers.LeverAdapter(), AB: providers.AshbyAdapter()}[url]
    with pytest.raises(SourceError, match="Unexpected"):
        adapter.fetch("example-co")


@respx.mock
def test_html_instead_of_json_is_a_source_error(sleeps):
    respx.get(GH).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(SourceError, match="unexpected response"):
        providers.GreenhouseAdapter().fetch("example-co")
    assert sleeps == []


# ── Retries ────────────────────────────────────────────────────────────────────────────────────
@respx.mock
def test_transient_5xx_is_retried_then_succeeds(sleeps):
    route = respx.get(GH).mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"jobs": []})]
    )
    r = providers.GreenhouseAdapter().fetch("example-co")
    assert route.call_count == 3 and len(sleeps) == 2 and r.jobs == []
    assert 0 <= sleeps[0] <= 1.0 and 0 <= sleeps[1] <= 2.0  # full-jitter exponential backoff


@respx.mock
def test_timeouts_retry_with_a_cap_then_fail(sleeps):
    route = respx.get(GH).mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(SourceError, match="Gave up after 3 attempts"):
        providers.GreenhouseAdapter().fetch("example-co")
    assert route.call_count == base.MAX_ATTEMPTS == 3 and len(sleeps) == 2


@respx.mock
def test_429_honours_retry_after_seconds_and_dates(sleeps):
    respx.get(GH).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),  # in the past → 0
            httpx.Response(200, json={"jobs": []}),
        ]
    )
    providers.GreenhouseAdapter().fetch("example-co")
    assert sleeps == [2.0, 0.0]


@respx.mock
def test_retry_after_is_capped(sleeps):
    respx.get(GH).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(200, json={"jobs": []})]
    )
    providers.GreenhouseAdapter().fetch("example-co")
    assert sleeps == [base.RETRY_AFTER_CAP_SECONDS]


@pytest.mark.parametrize(
    ("status", "message"), [(404, "Board not found"), (401, "HTTP 401"), (403, "HTTP 403"), (400, "HTTP 400")]
)
@respx.mock
def test_permanent_4xx_is_not_retried(sleeps, status, message):
    route = respx.get(GH).mock(return_value=httpx.Response(status))
    with pytest.raises(SourceError, match=message):
        providers.GreenhouseAdapter().fetch("example-co")
    assert route.call_count == 1 and sleeps == []


@respx.mock
def test_total_retry_budget_stops_early(sleeps, monkeypatch):
    clock = iter([0.0, 44.0, 44.0])  # start, then already near the 45 s budget
    monkeypatch.setattr(base, "_monotonic", lambda: next(clock))
    route = respx.get(GH).mock(return_value=httpx.Response(429, headers={"Retry-After": "5"}))
    with pytest.raises(SourceError, match="Gave up after 1 attempt"):
        providers.GreenhouseAdapter().fetch("example-co")
    assert route.call_count == 1 and sleeps == []


# ── URL validation ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://boards.greenhouse.io/x/jobs/1", True),
        ("http://example.com/job", True),
        ("  https://example.com/job  ", True),
        ("javascript:alert(1)", False),
        ("JAVASCRIPT:alert(1)", False),
        ("data:text/html,<b>x</b>", False),
        ("ftp://example.com/x", False),
        ("//example.com/x", False),
        ("https://", False),
        ("https://exa mple.com", False),
        ("", False),
        (None, False),
        (123, False),
    ],
)
def test_safe_url(url, ok):
    assert (safe_url(url) is not None) is ok


# ── Sync safety (through the API) ──────────────────────────────────────────────────────────────
class ScriptedBoard:
    """A board whose next response is set by the test (FetchResult, exception or tuple)."""

    provider = "greenhouse"

    def __init__(self):
        self.next = None

    def fetch(self, identifier):
        if isinstance(self.next, Exception):
            raise self.next
        return self.next


def _jobs(n, prefix="j"):
    return [job(f"{prefix}{i}", f"Engineer {i}", "Python. 2-4 years of experience") for i in range(n)]


@pytest.fixture
def board(user_api, monkeypatch):
    b = ScriptedBoard()
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", b)
    _onboard(user_api)
    return b


def _follow(api, board, jobs):
    board.next = FetchResult("Acme Payments", jobs, raw_count=len(jobs))
    api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    return api.get(f"{V}/job-sources").json()[0]["id"]


def _sync(api, sid, board, result):
    board.next = result
    return api.post(f"{V}/job-sources/{sid}/sync").json()


def _open(api):
    return sorted(i["title"] for i in api.get(f"{V}/jobs", params={"page_size": 100}).json()["items"])


def test_empty_response_after_listings_closes_nothing(user_api, board):
    sid = _follow(user_api, board, _jobs(3))
    s = _sync(user_api, sid, board, FetchResult("Acme Payments", [], raw_count=0))
    assert s["last_status"] == "warning" and "previously 3" in s["last_error"]
    assert s["last_job_count"] == 3  # the trusted baseline is kept
    assert len(_open(user_api)) == 3


def test_first_sync_of_an_empty_board_is_ok(user_api, board):
    sid = _follow(user_api, board, [])
    s = user_api.get(f"{V}/job-sources").json()[0]
    assert s["id"] == sid and s["last_status"] == "ok" and s["last_job_count"] == 0


def test_suspicious_drop_closes_nothing_but_a_normal_drop_does(user_api, board):
    jobs = _jobs(12)
    sid = _follow(user_api, board, jobs)
    s = _sync(user_api, sid, board, FetchResult("Acme Payments", jobs[:5], raw_count=5))  # 12 → 5
    assert s["last_status"] == "warning" and "less than half" in s["last_error"] and len(_open(user_api)) == 12
    s = _sync(user_api, sid, board, FetchResult("Acme Payments", jobs[:7], raw_count=7))  # 12 → 7: plausible
    assert s["last_status"] == "ok" and s["last_job_count"] == 7 and len(_open(user_api)) == 7


def test_small_boards_can_shrink_normally(user_api, board):
    jobs = _jobs(4)
    sid = _follow(user_api, board, jobs)
    s = _sync(user_api, sid, board, FetchResult("Acme Payments", jobs[:1], raw_count=1))
    assert s["last_status"] == "ok" and len(_open(user_api)) == 1


def test_invalid_listing_is_skipped_and_not_closed(user_api, board):
    jobs = _jobs(3)
    sid = _follow(user_api, board, jobs)
    res = FetchResult(
        "Acme Payments", jobs[:2], skipped=[base.Skipped("j2", "listing has no valid http(s) URL")], raw_count=3
    )
    s = _sync(user_api, sid, board, res)
    assert s["last_status"] == "partial" and "1 listing(s) skipped" in s["last_error"]
    assert len(_open(user_api)) == 3  # j2 kept open while it's temporarily unusable


def test_unidentifiable_listing_means_nothing_is_closed(user_api, board):
    jobs = _jobs(3)
    sid = _follow(user_api, board, jobs)
    res = FetchResult("Acme Payments", jobs[:2], skipped=[base.Skipped(None, "listing has no id")], raw_count=3)
    s = _sync(user_api, sid, board, res)
    assert s["last_status"] == "warning" and "couldn't be identified" in s["last_error"]
    assert len(_open(user_api)) == 3


def test_temporary_url_loss_in_a_legacy_adapter_keeps_the_job(user_api, board):
    jobs = _jobs(2)
    sid = _follow(user_api, board, jobs)
    broken = _jobs(2)
    broken[1].url = ""
    s = _sync(user_api, sid, board, ("Acme Payments", broken))  # older tuple-style adapters still work
    assert s["last_status"] == "ok" and len(_open(user_api)) == 2


def test_duplicate_ids_create_one_record(user_api, board):
    jobs = _jobs(1)
    sid = _follow(user_api, board, jobs + _jobs(1))  # the same id twice
    assert user_api.get(f"{V}/job-sources").json()[0]["id"] == sid
    assert _open(user_api) == ["Engineer 0"]


def test_board_errors_and_adapter_crashes_change_nothing(user_api, board):
    sid = _follow(user_api, board, _jobs(2))
    s = _sync(user_api, sid, board, SourceError("The job board returned HTTP 503. Gave up after 3 attempts."))
    assert s["last_status"] == "error" and "503" in s["last_error"] and len(_open(user_api)) == 2
    r = user_api.post(f"{V}/job-sources/{sid}/sync")  # still serves the request after an adapter bug
    board.next = KeyError("id")
    r = user_api.post(f"{V}/job-sources/{sid}/sync")
    assert r.status_code == 200 and r.json()["last_status"] == "error" and "KeyError" in r.json()["last_error"]
    assert len(_open(user_api)) == 2


@respx.mock
def test_end_to_end_sync_with_a_real_adapter_and_fixture(user_api, monkeypatch, sleeps):
    _onboard(user_api)
    respx.get("https://boards-api.greenhouse.io/v1/boards/example-co/jobs").mock(
        return_value=httpx.Response(200, json=fixture("greenhouse"))
    )
    user_api.post(f"{V}/job-sources", json={"provider": "greenhouse", "identifier": "example-co"})
    s = user_api.get(f"{V}/job-sources").json()[0]
    # One listing has no id, so the response can't be trusted as complete: warning, nothing closed.
    assert s["last_status"] == "warning" and s["last_job_count"] == 2
    assert "3 listing(s) skipped" in s["last_error"] and "couldn't be identified" in s["last_error"]
    items = user_api.get(f"{V}/jobs").json()["items"]
    assert sorted(i["title"] for i in items) == ["Backend Engineer", "Data Analyst"]
    assert all(i["url"].startswith("https://") for i in items)


# ── Saved/applied decisions are never silently recomputed ──────────────────────────────────────
def test_saved_decision_is_kept_and_marked_outdated_when_the_listing_changes(user_api, board):
    jobs = _jobs(2)
    sid = _follow(user_api, board, jobs)
    ids = {i["title"]: i["id"] for i in user_api.get(f"{V}/jobs").json()["items"]}
    saved, other = ids["Engineer 0"], ids["Engineer 1"]
    user_api.put(f"{V}/jobs/{saved}/state", json={"state": "saved"})
    before = user_api.get(f"{V}/jobs/{saved}").json()["eligibility_detail"]
    other_before = user_api.get(f"{V}/jobs/{other}").json()["eligibility_detail"]["evaluated_at"]

    changed = _jobs(2)
    for j in changed:
        j.experience_min, j.experience_max = 8.0, None  # the board now asks for much more experience
    _sync(user_api, sid, board, FetchResult("Acme Payments", changed, raw_count=2))

    kept = user_api.get(f"{V}/jobs/{saved}").json()
    assert kept["eligibility_detail"]["evaluated_at"] == before["evaluated_at"]  # not recomputed
    assert kept["eligibility_detail"]["verdict"] == before["verdict"]
    assert kept["eligibility_outdated"] is True and kept["eligibility_outdated_reasons"] == ["listing"]
    other_now = user_api.get(f"{V}/jobs/{other}").json()
    assert other_now["eligibility_detail"]["evaluated_at"] != other_before  # unsaved jobs refresh
    assert other_now["eligibility_outdated"] is False

    prof = user_api.get(f"{V}/profile").json()["profile"]
    user_api.put(f"{V}/profile", json={**prof, "years_experience": 9})
    assert user_api.get(f"{V}/jobs/{saved}").json()["eligibility_outdated_reasons"] == ["profile", "listing"]

    fresh = user_api.post(f"{V}/jobs/{saved}/reevaluate").json()  # explicit user action
    assert (
        fresh["eligibility_outdated"] is False and fresh["eligibility_detail"]["evaluated_at"] != before["evaluated_at"]
    )


def test_resync_without_changes_does_not_mark_saved_jobs_outdated(user_api, board):
    sid = _follow(user_api, board, _jobs(1))
    jid = user_api.get(f"{V}/jobs").json()["items"][0]["id"]
    user_api.put(f"{V}/jobs/{jid}/state", json={"state": "saved"})
    _sync(user_api, sid, board, FetchResult("Acme Payments", _jobs(1), raw_count=1))
    assert user_api.get(f"{V}/jobs/{jid}").json()["eligibility_outdated"] is False


def test_lever_pay_without_currency_goes_to_manual_review():
    from app.domain.matching import compute_match
    from app.domain.salary_policy import SalaryPolicy
    from app.services.jobs import job_facts
    from tests.test_eligibility_policy import CAND, check

    n = job("x", "Support Engineer", "Help customers.")
    n.salary_min, n.salary_max, n.salary_currency, n.salary_period, n.salary_disclosure = (
        9e5,
        1.2e6,
        None,
        "year",
        "verified",
    )
    from app.models import Job

    j = Job(
        **{
            k: getattr(n, k)
            for k in (
                "title",
                "required_skills",
                "preferred_skills",
                "experience_min",
                "experience_max",
                "location",
                "work_mode",
                "seniority",
                "employment_type",
                "is_internship",
                "salary_min",
                "salary_max",
                "salary_currency",
                "salary_period",
                "salary_disclosure",
                "salary_text",
                "description_text",
            )
        }
    )
    e = compute_match(job_facts(j), CAND, SalaryPolicy()).eligibility
    assert check(e, "SALARY")["code"] == "SAL_CURRENCY_UNKNOWN" and "salary currency" in e["missing_information"]
