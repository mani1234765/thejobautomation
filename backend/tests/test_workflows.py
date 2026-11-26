import io

import pytest
from docx import Document

from app.integrations.sources import providers
from tests.fixtures import build_docx_resume
from tests.helpers import JD_BACKEND, FakeAdapter, ScriptedLLM, job


@pytest.fixture
def fake_board(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", adapter)
    return adapter


def _upload(api):
    r = api.post("/api/v1/resumes", files={"file": ("Asha CV.docx", build_docx_resume(), "application/octet-stream")})
    assert r.status_code == 201, r.text
    return r.json()


def _onboard(api):
    resume = _upload(api)
    assert api.post(f"/api/v1/resumes/{resume['id']}/apply-to-profile", json={"replace": True}).status_code == 200
    prof = api.get("/api/v1/profile").json()["profile"]
    prof.update(
        target_titles=["Backend Engineer"],
        preferred_locations=["Bengaluru"],
        work_modes=["hybrid", "remote"],
        experience_levels=["3-5", "5+"],
        employment_types=["full_time"],
        years_experience=4,
    )
    assert api.put("/api/v1/profile", json=prof).status_code == 200
    assert api.post("/api/v1/profile/verify").status_code == 204
    assert api.post("/api/v1/onboarding/complete").status_code == 204
    return resume


def _add_board(api):
    r = api.post(
        "/api/v1/job-sources", json={"provider": "greenhouse", "identifier": "https://boards.greenhouse.io/acme"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["identifier"] == "acme" and r.json()["last_status"] == "ok"
    return r.json()


# ── Resume ─────────────────────────────────────────────────────────────────────────────────────
def test_resume_upload_parse_and_prefill(user_api):
    resume = _upload(user_api)
    assert resume["parse_status"] == "parsed" and resume["is_primary"]
    assert {s["canonical_type"] for s in resume["sections"]} >= {"summary", "experience", "skills", "education"}
    again = _upload(user_api)  # identical file is de-duplicated
    assert again["id"] == resume["id"]
    summary = user_api.post(f"/api/v1/resumes/{resume['id']}/apply-to-profile", json={"replace": True}).json()
    assert summary["experiences"] == 3
    full = user_api.get("/api/v1/profile").json()
    cats = sorted(e["category"] for e in full["experiences"])
    assert cats == ["internship", "professional", "professional"]
    assert all(not e["verified"] for e in full["experiences"])  # imported data starts unverified
    assert full["profile"]["phone"]
    # Re-applying replaces rather than duplicating.
    user_api.post(f"/api/v1/resumes/{resume['id']}/apply-to-profile", json={"replace": True})
    assert len(user_api.get("/api/v1/profile").json()["experiences"]) == 3


# ── Jobs ───────────────────────────────────────────────────────────────────────────────────────
def test_job_discovery_matching_and_filters(user_api, fake_board):
    _onboard(user_api)
    _add_board(user_api)
    page = user_api.get("/api/v1/jobs").json()
    assert page["total"] == 2 and page["sources"] == 1
    top = page["items"][0]
    assert top["title"] == "Backend Engineer" and top["match_score"] > page["items"][1]["match_score"]
    assert top["salary_disclosure"] == "advertised" and top["salary_min"] == 1_800_000
    detail = user_api.get(f"/api/v1/jobs/{top['id']}").json()
    assert detail["match"]["factors"] and "Python" in detail["matched_skills"]
    assert detail["eligibility_detail"]["salary"]["decision"] == "manual_review"  # advertised pay → review

    assert user_api.get("/api/v1/jobs", params={"q": "account"}).json()["total"] == 1
    assert top["match_score"] >= 90  # every required skill and preference lines up
    assert user_api.get("/api/v1/jobs", params={"q": "account", "min_score": 50}).json()["total"] == 0
    assert user_api.get("/api/v1/jobs", params={"skills": "Salesforce"}).json()["total"] == 1
    assert user_api.put(f"/api/v1/jobs/{top['id']}/state", json={"state": "dismissed"}).status_code == 204
    assert user_api.get("/api/v1/jobs").json()["total"] == 1
    assert user_api.get("/api/v1/jobs", params={"state": "dismissed"}).json()["total"] == 1
    cmp = user_api.get("/api/v1/jobs/compare", params={"ids": ",".join(i["id"] for i in page["items"])})
    assert cmp.status_code == 200 and len(cmp.json()) == 2


def test_sync_closes_removed_postings_and_reports_errors(user_api, fake_board):
    src = _add_board(user_api)
    fake_board.jobs = fake_board.jobs[:1]
    r = user_api.post(f"/api/v1/job-sources/{src['id']}/sync").json()
    assert r["last_job_count"] == 1
    assert user_api.get("/api/v1/jobs").json()["total"] == 1
    fake_board.error = "Board not found. Check the company's board identifier."
    r = user_api.post(f"/api/v1/job-sources/{src['id']}/sync").json()
    assert r["last_status"] == "error" and "Board not found" in r["last_error"]


def test_jobs_visible_only_through_followed_sources(user_api, fake_board):
    _add_board(user_api)
    job_id = user_api.get("/api/v1/jobs").json()["items"][0]["id"]
    from tests.conftest import make_client, register

    other = make_client()
    register(other, email="o@x.io")
    assert other.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert other.get("/api/v1/jobs").json()["total"] == 0


# ── Tailoring ──────────────────────────────────────────────────────────────────────────────────
def test_tailoring_without_ai_reorders_skills_and_preserves_docx(user_api, fake_board):
    fake_board.jobs = [job("9", "Data Engineer", "Requirements\n• 3+ years of experience with SQL and Kafka")]
    resume = _onboard(user_api)
    _add_board(user_api)
    job_id = user_api.get("/api/v1/jobs").json()["items"][0]["id"]
    v = user_api.post(f"/api/v1/resumes/{resume['id']}/tailor", json={"job_id": job_id}).json()
    assert v["generated_by"] == "deterministic" and v["analysis"]["ai_note"]
    assert set(v["analysis"]["matched_required"]) == {"SQL", "Kafka"}
    reorder = [c for c in v["changes"] if c["kind"] == "reorder"]
    assert reorder and all(
        sorted(c["original"].split(":")[1].replace(" ", "").split(","))
        == sorted(c["proposed"].split(":")[1].replace(" ", "").split(","))
        for c in reorder
    )
    assert user_api.post(f"/api/v1/resume-versions/{v['id']}/approve").status_code == 400  # pending changes
    for c in v["changes"]:
        user_api.put(f"/api/v1/resume-versions/{v['id']}/changes/{c['id']}", json={"status": "accepted"})
    approved = user_api.post(f"/api/v1/resume-versions/{v['id']}/approve").json()
    assert approved["status"] == "approved"
    out = user_api.get(f"/api/v1/resume-versions/{v['id']}/download")
    doc = Document(io.BytesIO(out.content))
    assert doc.sections[0].header.paragraphs[0].text == "Asha Verma — Resume"
    assert doc.tables[0].cell(0, 1).text == "2017 – 2021"
    # The original upload is untouched.
    original = user_api.get(f"/api/v1/resumes/{resume['id']}/download").content
    assert original == build_docx_resume()


def test_ai_tailoring_flags_fabrication(user_api, fake_board, monkeypatch):
    resume = _onboard(user_api)
    _add_board(user_api)
    job_id = user_api.get("/api/v1/jobs").json()["items"][0]["id"]
    detail = user_api.get(f"/api/v1/resumes/{resume['id']}").json()
    exp_ids = next(s for s in detail["sections"] if s["canonical_type"] == "experience")["paragraph_ids"]
    bullet = exp_ids[2]  # "Responsible for CI/CD pipelines…"
    llm = ScriptedLLM(
        [
            {
                "data": {
                    "summary_of_fit": "Strong backend fit.",
                    "changes": [
                        {
                            "paragraph_id": bullet,
                            "proposed": "Automated CI/CD pipelines with GitHub Actions and Docker.",
                            "rationale": "Stronger verb.",
                        },
                        {
                            "paragraph_id": exp_ids[1],
                            "proposed": "Built Kubernetes microservices handling 9M requests/day on AWS.",
                            "rationale": "Mentions Kubernetes.",
                        },
                        {"paragraph_id": "b9999", "proposed": "Injected", "rationale": "not a real paragraph"},
                    ],
                }
            }
        ]
    )
    from app.ai import service as ai

    monkeypatch.setattr(ai, "status", lambda: {"configured": True})
    monkeypatch.setattr(ai, "provider_for", lambda user: llm)
    v = user_api.post(f"/api/v1/resumes/{resume['id']}/tailor", json={"job_id": job_id}).json()
    rewrites = {c["paragraph_id"]: c for c in v["changes"] if c["kind"] == "rewrite"}
    assert set(rewrites) == {bullet, exp_ids[1]}  # unknown paragraph id dropped
    assert rewrites[bullet]["flags"] == []
    flags = " ".join(rewrites[exp_ids[1]]["flags"])
    assert "9m" in flags  # invented metric flagged (Kubernetes is genuinely in the resume, so not flagged)
    # Job description is passed as untrusted data.
    assert "<untrusted_document" in llm.requests[0]["messages"][0]["content"]
    # A flagged change can't be accepted without explicit acknowledgement.
    cid = rewrites[exp_ids[1]]["id"]
    r = user_api.put(f"/api/v1/resume-versions/{v['id']}/changes/{cid}", json={"status": "accepted"})
    assert r.status_code == 400
    r = user_api.put(
        f"/api/v1/resume-versions/{v['id']}/changes/{cid}", json={"status": "accepted", "acknowledge_flags": True}
    )
    assert r.status_code == 200


# ── Applications ───────────────────────────────────────────────────────────────────────────────
def _application(api):
    job_id = api.get("/api/v1/jobs").json()["items"][0]["id"]
    r = api.post("/api/v1/applications", json={"job_id": job_id})
    assert r.status_code == 201
    dup = api.post("/api/v1/applications", json={"job_id": job_id})
    assert dup.json()["id"] == r.json()["id"]  # duplicate prevention
    return r.json()


def test_application_lifecycle_requires_approval_and_acknowledgement(user_api, fake_board):
    _onboard(user_api)
    _add_board(user_api)
    app = _application(user_api)
    aid = app["id"]
    # Can't jump straight to submitted.
    assert user_api.post(f"/api/v1/applications/{aid}/transition", json={"to": "submitted"}).status_code == 400
    prep = user_api.post(
        f"/api/v1/applications/{aid}/prepare",
        json={"questions": ["Why do you want this job?"], "generate_cover_letter": True},
    ).json()
    detail = prep["application"]
    assert detail["status"] == "preparing" and detail["resume_id"]
    assert any("AI provider" in n for n in prep["notes"])
    unknown = [a for a in detail["answers"] if a["confidence"] == "unknown"]
    assert any(a["question"] == "Why do you want this job?" for a in unknown)  # never invented
    assert user_api.post(f"/api/v1/applications/{aid}/request-approval").status_code == 400  # unresolved answers
    for a in unknown:
        user_api.put(f"/api/v1/applications/{aid}/answers/{a['id']}", json={"answer": "My own answer"})
    approval = user_api.post(f"/api/v1/applications/{aid}/request-approval").json()
    assert approval["status"] == "pending"
    # Starting a submission before approval is refused.
    assert user_api.post(f"/api/v1/applications/{aid}/submission", json={"action": "start"}).status_code == 400

    # Editing after the request voids it.
    a0 = detail["answers"][0]
    user_api.put(f"/api/v1/applications/{aid}/answers/{a0['id']}", json={"answer": "Asha V."})
    r = user_api.post(f"/api/v1/approvals/{approval['id']}/decide", json={"approve": True})
    assert r.status_code == 400
    approval = user_api.post(f"/api/v1/applications/{aid}/request-approval").json()
    assert user_api.post(f"/api/v1/approvals/{approval['id']}/decide", json={"approve": True}).status_code == 200

    d = user_api.post(f"/api/v1/applications/{aid}/submission", json={"action": "start"}).json()
    assert d["status"] == "approved" and d["submission_state"] == "attempted"  # not "submitted" yet
    again = user_api.post(f"/api/v1/applications/{aid}/submission", json={"action": "start"}).json()
    starts = [e for e in again["events"] if e["kind"] == "submission" and e["detail"].get("action") == "start"]
    assert len(starts) == 1  # idempotent
    d = user_api.post(
        f"/api/v1/applications/{aid}/submission", json={"action": "confirm", "reference": "GH-123"}
    ).json()
    assert d["status"] == "submitted" and d["submission_state"] == "acknowledged" and d["follow_up_at"]
    d = user_api.post(f"/api/v1/applications/{aid}/transition", json={"to": "interview"}).json()
    assert d["status"] == "interview"
    notes = user_api.get("/api/v1/notifications").json()
    assert {"approval_requested", "application_submitted"} <= {n["kind"] for n in notes["items"]}


def test_failed_submission_is_not_success_and_daily_limit(user_api, fake_board):
    _onboard(user_api)
    _add_board(user_api)
    s = user_api.get("/api/v1/settings").json()
    s["daily_application_limit"] = 1
    user_api.put("/api/v1/settings", json=s)
    aid = _application(user_api)["id"]
    detail = user_api.post(f"/api/v1/applications/{aid}/prepare", json={}).json()["application"]
    for a in detail["answers"]:
        if a["needs_review"]:
            user_api.put(f"/api/v1/applications/{aid}/answers/{a['id']}", json={"answer": "n/a"})
    ap = user_api.post(f"/api/v1/applications/{aid}/request-approval").json()
    user_api.post(f"/api/v1/approvals/{ap['id']}/decide", json={"approve": True})
    user_api.post(f"/api/v1/applications/{aid}/submission", json={"action": "start"})
    d = user_api.post(f"/api/v1/applications/{aid}/submission", json={"action": "fail", "reason": "Form error"}).json()
    assert d["status"] == "failed" and d["submission_state"] == "failed" and d["submitted_at"] is None
    dash = user_api.get("/api/v1/dashboard").json()
    assert dash["metrics"]["applications_submitted"] == 0
    # Retrying counts against the daily limit (1 already used).
    r = user_api.post(f"/api/v1/applications/{aid}/submission", json={"action": "start"})
    assert r.status_code == 409 and "Daily limit" in r.json()["error"]["message"]


def test_mcp_token_cannot_bypass_approval(user_api, fake_board):
    _onboard(user_api)
    _add_board(user_api)
    aid = _application(user_api)["id"]
    detail = user_api.post(f"/api/v1/applications/{aid}/prepare", json={}).json()["application"]
    for a in detail["answers"]:
        if a["needs_review"]:
            user_api.put(f"/api/v1/applications/{aid}/answers/{a['id']}", json={"answer": "n/a"})
    ap = user_api.post(f"/api/v1/applications/{aid}/request-approval").json()
    from fastapi.testclient import TestClient

    from app.main import create_app

    read_tok = user_api.post("/api/v1/auth/tokens", json={"name": "r", "scopes": ["applications:write"]}).json()[
        "token"
    ]
    c = TestClient(create_app(), headers={"Authorization": f"Bearer {read_tok}"})
    assert c.post(f"/api/v1/approvals/{ap['id']}/decide", json={"approve": True}).status_code == 403
    assert c.post(f"/api/v1/applications/{aid}/transition", json={"to": "approved"}).status_code == 400
    ap_tok = user_api.post("/api/v1/auth/tokens", json={"name": "a", "scopes": ["approvals:write"]}).json()["token"]
    c2 = TestClient(create_app(), headers={"Authorization": f"Bearer {ap_tok}"})
    r = c2.post(f"/api/v1/approvals/{ap['id']}/decide", json={"approve": True})
    assert r.status_code == 200 and r.json()["decided_via"] == "mcp"
    # Even with approval granted, an MCP client can never submit.
    assert c2.post(f"/api/v1/applications/{aid}/submission", json={"action": "start"}).status_code == 403


# ── Automation ─────────────────────────────────────────────────────────────────────────────────
def test_discovery_and_preparation_automations(user_api, fake_board, db):
    _onboard(user_api)
    _add_board(user_api)
    fake_board.jobs = [*fake_board.jobs, job("3", "Senior Backend Engineer", JD_BACKEND)]
    cfg = user_api.post(
        "/api/v1/automations",
        json={
            "name": "Daily",
            "kind": "discovery",
            "interval_minutes": 60,
            "filters": {"min_score": 40, "notify_min_score": 40},
        },
    ).json()
    assert cfg["state"] == "active" and cfg["next_run_at"]
    assert (
        user_api.post(
            "/api/v1/automations",
            json={"name": "x", "kind": "discovery", "interval_minutes": 60, "filters": {"rm_rf": True}},
        ).status_code
        == 400
    )
    user_api.post(f"/api/v1/automations/{cfg['id']}/run")
    user_api.post(f"/api/v1/automations/{cfg['id']}/run")  # same minute → idempotent

    from app.worker.main import tick

    assert tick(db) is True
    assert tick(db) is False  # nothing else queued
    ex = user_api.get("/api/v1/automations/executions/recent").json()
    assert len(ex) == 1 and ex[0]["status"] == "succeeded" and ex[0]["log"][0]["event"] == "started"
    assert ex[0]["stats"]["new_jobs"] == 1
    apps = user_api.get("/api/v1/applications").json()
    assert apps and all(a["status"] == "shortlisted" for a in apps)

    prep = user_api.post(
        "/api/v1/automations",
        json={"name": "Prep", "kind": "preparation", "interval_minutes": 60, "filters": {"min_score": 40}},
    ).json()
    user_api.post(f"/api/v1/automations/{prep['id']}/run")
    tick(db)
    apps = user_api.get("/api/v1/applications").json()
    assert {a["status"] for a in apps} <= {"preparing", "awaiting_approval", "shortlisted"}
    assert not any(a["status"] == "submitted" for a in apps)  # automation never submits

    paused = user_api.post(f"/api/v1/automations/{cfg['id']}/pause").json()
    assert paused["state"] == "paused" and paused["next_run_at"] is None


# ── Assistant ──────────────────────────────────────────────────────────────────────────────────
def test_assistant_requires_ai_provider(user_api):
    cid = user_api.post("/api/v1/assistant/conversations").json()["id"]
    r = user_api.post(f"/api/v1/assistant/conversations/{cid}/messages", json={"content": "hi"})
    assert r.status_code == 503


def test_assistant_tool_loop(user_api, fake_board, monkeypatch):
    _onboard(user_api)
    _add_board(user_api)
    llm = ScriptedLLM(
        [
            {"text": "", "tool_calls": [{"id": "t1", "name": "search_jobs", "arguments": {"query": "backend"}}]},
            {"text": "Your best match is Backend Engineer at Acme Payments."},
        ]
    )
    from app.ai import service as ai

    monkeypatch.setattr(ai, "provider_for", lambda user: llm)
    cid = user_api.post("/api/v1/assistant/conversations").json()["id"]
    msgs = user_api.post(
        f"/api/v1/assistant/conversations/{cid}/messages", json={"content": "What should I apply to?"}
    ).json()
    assert msgs[-1]["content"].startswith("Your best match") and msgs[-1]["tools_used"] == ["search_jobs"]
    second = llm.requests[1]["messages"]
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert "Backend Engineer" in tool_msg["content"]
    assert set(llm.requests[0]["tools"]) >= {"search_jobs", "get_profile"}
    assert not any(t in llm.requests[0]["tools"] for t in ("submit", "approve_application", "send_email"))
    usage = user_api.get("/api/v1/ai/usage").json()
    assert usage["by_feature"][0]["calls"] == 2 and usage["by_feature"][0]["cost_usd"] > 0


def test_ai_budget_enforced(user_api, monkeypatch):
    from app.ai import service as ai
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_daily_budget_usd", 0.0)
    monkeypatch.setattr(ai, "provider_for", lambda user: ScriptedLLM([{"text": "x"}]))
    cid = user_api.post("/api/v1/assistant/conversations").json()["id"]
    r = user_api.post(f"/api/v1/assistant/conversations/{cid}/messages", json={"content": "hi"})
    assert r.status_code == 502 and "budget" in r.json()["error"]["message"]


# ── Interviews, comms, insights ────────────────────────────────────────────────────────────────
def test_interview_prep_without_ai(user_api, fake_board):
    _onboard(user_api)
    _add_board(user_api)
    job_id = user_api.get("/api/v1/jobs").json()["items"][0]["id"]
    s = user_api.post("/api/v1/interview/sessions", json={"job_id": job_id}).json()
    assert s["generated_by"] == "template"
    assert all(q["provenance"] in ("skill_template", "standard") for q in s["questions"])
    q = s["questions"][-1]
    fb = user_api.post(
        f"/api/v1/interview/sessions/{s['id']}/answer",
        json={
            "question_id": q["id"],
            "answer": "When our checkout broke at my job, I had to fix it. I built a retry queue "
            "and as a result failures dropped 30%.",
        },
    ).json()
    assert fb["feedback"]["method"] == "heuristic" and all(fb["feedback"]["star"].values())
    assert user_api.get(f"/api/v1/interview/sessions/{s['id']}").json()["progress"]["answered"] == 1


def test_email_classifier():
    from app.domain.email_classifier import classify

    assert classify("Thank you for applying to Acme", "").category == "application_confirmation"
    assert classify("Next steps", "We'd like to schedule an interview with you").category == "interview_invitation"
    assert classify("Your application", "Unfortunately we are not moving forward").category == "rejection"
    assert classify("Weekend sale!", "50% off shoes").category == "other"


def test_dashboard_and_analytics_reflect_real_records(user_api, fake_board):
    empty = user_api.get("/api/v1/dashboard").json()
    assert empty["metrics"]["jobs_discovered"] == 0 and empty["recommended"] == []
    assert any(a["title"] == "Upload your resume" for a in empty["insights"]["next_actions"])
    _onboard(user_api)
    _add_board(user_api)
    _application(user_api)
    d = user_api.get("/api/v1/dashboard").json()
    assert d["metrics"]["jobs_discovered"] == 2
    assert next(p for p in d["pipeline"] if p["status"] == "shortlisted")["count"] == 1
    assert sum(x["discovered"] for x in d["activity"]) == 2
    assert d["insights"]["skill_demand"]["jobs_analysed"] == 2
    a = user_api.get("/api/v1/analytics").json()
    assert a["totals"]["applications"] == 1 and a["conversion"]["application_to_interview"] is None
    assert a["definitions"]["application_to_interview"]


def test_daily_summary_only_when_there_is_news(user_api, fake_board, db):
    from sqlalchemy import select

    from app.models import Notification
    from app.worker.main import daily_summaries

    assert daily_summaries(db) == 0  # nothing to report → nothing sent
    _onboard(user_api)
    _add_board(user_api)
    assert daily_summaries(db) == 1
    n = db.scalar(select(Notification).where(Notification.kind == "daily_summary"))
    assert "new matching job" in n.body
    assert daily_summaries(db) == 0  # at most once per 24h
