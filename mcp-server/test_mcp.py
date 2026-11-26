import asyncio

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

import jobpilot_mcp as m

BASE = "http://jp.test/api/v1"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(m, "api", m.JobPilotAPI("http://jp.test", "jp_testtoken"))


def call(name, args=None):
    return asyncio.run(m.server.call_tool(name, args or {}))


def test_expected_tools_registered():
    names = {t.name for t in asyncio.run(m.server.list_tools())}
    assert {"search_jobs", "get_job_details", "get_user_profile", "analyse_resume", "analyse_job_description",
            "calculate_job_match", "tailor_resume", "generate_cover_letter", "prepare_application_answers",
            "list_applications", "get_application_status", "request_approval", "pause_automation",
            "resume_automation", "get_career_insights"} <= names
    # No tool can submit an application, send messages or grant approval.
    assert not any("submit" in n or "send" in n for n in names)
    assert not any(n.startswith(("approve", "decide", "reject")) for n in names)


@respx.mock
def test_search_jobs_forwards_token_and_filters():
    route = respx.get(f"{BASE}/jobs").mock(return_value=httpx.Response(200, json={"total": 1, "items": [
        {"id": "abc12345", "title": "Backend Engineer", "company": "Acme", "match_score": 88}]}))
    result = call("search_jobs", {"query": "backend", "work_modes": ["remote"], "limit": 5})
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer jp_testtoken"
    assert req.url.params["work_modes"] == "remote" and req.url.params["page_size"] == "5"
    assert result.structured_content["total"] == 1


@respx.mock
def test_api_errors_become_tool_errors():
    respx.post(f"{BASE}/applications/abcdefgh/request-approval").mock(return_value=httpx.Response(
        400, json={"error": {"message": "Only applications being prepared can be sent for approval.", "status": 400}}))
    with pytest.raises(ToolError, match="being prepared"):
        call("request_approval", {"application_id": "abcdefgh"})


@respx.mock
def test_request_approval_creates_a_pending_request_only():
    route = respx.post(f"{BASE}/applications/abcdefgh/request-approval").mock(
        return_value=httpx.Response(200, json={"id": "appr1234", "status": "pending"}))
    decide = respx.post(url__regex=rf"{BASE}/approvals/.*").mock(return_value=httpx.Response(200, json={}))
    result = call("request_approval", {"application_id": "abcdefgh"})
    assert route.called and not decide.called
    assert result.structured_content["status"] == "pending"


def test_input_validation_rejects_bad_ids():
    with pytest.raises(ToolError, match="pattern"):
        call("get_job_details", {"job_id": "../../etc/passwd"})


def test_missing_token():
    m.api.token = None
    with pytest.raises(ToolError):
        m.api.request("GET", "/profile")


def test_http_transport_refuses_non_loopback_hosts():
    with pytest.raises(SystemExit, match="only loopback"):
        m.http_options("0.0.0.0", 8765)
    opts = m.http_options("127.0.0.1", 8765)
    sec = opts["transport_security"]
    assert sec.enable_dns_rebinding_protection and "127.0.0.1:8765" in sec.allowed_hosts


def test_api_errors_never_echo_the_token(monkeypatch):
    import httpx as _httpx

    with respx.mock:
        respx.get(f"{BASE}/profile").mock(return_value=_httpx.Response(500, text="internal"))
        with pytest.raises(ToolError) as e:
            m.api.request("GET", "/profile")
    assert "jp_testtoken" not in str(e.value)
