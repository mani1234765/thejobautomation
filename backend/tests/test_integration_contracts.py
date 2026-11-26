"""Offline contract tests for external integrations.

These pin the exact requests JobPilot sends to each provider and how it handles their responses
(success and failure) using mocked HTTP transports. They do NOT prove the live providers accept the
credentials — that is what `python -m app.ops.verify_integrations` does when credentials exist.
"""

import json
import time

import httpx
import httpx2
import pytest
import respx

from app.ai.provider import AnthropicProvider, LLMError, LLMRefusal, OpenAICompatibleProvider, ToolSpec
from app.config import Settings

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _settings(**kw) -> Settings:
    base = dict(llm_provider="anthropic", llm_model="claude-opus-5-5", llm_api_key="test-key-not-real")
    base.update(kw)
    return Settings(**base)


def _anthropic_message(content, stop_reason="end_turn", model="claude-opus-5-5"):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return httpx2.Response(status, json=body, headers={"request-id": "req_test"})


def _anthropic(responses, model="claude-opus-5-5"):
    rec = Recorder(responses)
    client = httpx2.Client(transport=httpx2.MockTransport(rec))
    return AnthropicProvider(_settings(llm_model=model), model, http_client=client), rec


# ── Anthropic ──────────────────────────────────────────────────────────────────────────────────
def test_anthropic_structured_output_request_shape():
    llm, rec = _anthropic([(200, _anthropic_message([{"type": "text", "text": '{"ok": true}'}]))])
    result = llm.complete(system="sys", messages=[{"role": "user", "content": "hi"}], json_schema=SCHEMA)
    assert result.data == {"ok": True} and result.input_tokens == 120 and result.cost_usd > 0
    req = rec.requests[0]
    body = json.loads(req.content)
    assert req.url.path == "/v1/messages"
    assert req.headers["authorization"] == "Bearer test-key-not-real"
    assert req.headers["anthropic-version"]  # pinned by the SDK
    assert "server-side-fallback-2026-07-01" in req.headers.get("anthropic-beta", "")
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert "thinking" not in body and "temperature" not in body  # unsupported on current models


def test_anthropic_tool_use_round_trip_replays_raw_content():
    tool_turn = _anthropic_message(
        [
            {"type": "text", "text": ""},
            {"type": "tool_use", "id": "tu_1", "name": "search_jobs", "input": {"query": "backend"}},
        ],
        stop_reason="tool_use",
    )
    llm, rec = _anthropic([(200, tool_turn), (200, _anthropic_message([{"type": "text", "text": "Done"}]))])
    tools = [ToolSpec("search_jobs", "Search", {"type": "object", "properties": {"query": {"type": "string"}}})]
    first = llm.complete(system="s", messages=[{"role": "user", "content": "find jobs"}], tools=tools)
    assert first.tool_calls == [{"id": "tu_1", "name": "search_jobs", "arguments": {"query": "backend"}}]
    history = [
        {"role": "user", "content": "find jobs"},
        first.as_assistant_message(),
        {"role": "tool", "tool_call_id": "tu_1", "content": '{"total": 1}'},
    ]
    llm.complete(system="s", messages=history, tools=tools)
    body = json.loads(rec.requests[1].content)
    assert body["tool_choice"] == {"type": "auto"}  # forced tool choice is rejected by current models
    assert body["messages"][1]["content"] == first.raw_assistant  # exact blocks replayed, append-only
    assert body["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "tu_1",
        "content": '{"total": 1}',
    }


def test_anthropic_refusal_and_errors():
    llm, _ = _anthropic([(200, _anthropic_message([], stop_reason="refusal"))])
    with pytest.raises(LLMRefusal):
        llm.complete(system="s", messages=[{"role": "user", "content": "x"}])
    llm, _ = _anthropic([(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}})])
    with pytest.raises(LLMError) as e:
        llm.complete(system="s", messages=[{"role": "user", "content": "x"}])
    assert not e.value.retryable and "test-key" not in str(e.value)


def test_anthropic_models_without_fallback_use_plain_endpoint():
    llm, rec = _anthropic(
        [(200, _anthropic_message([{"type": "text", "text": "ok"}], model="claude-haiku-5-5"))],
        model="claude-haiku-5-5",
    )
    llm.complete(system="s", messages=[{"role": "user", "content": "x"}])
    assert "fallbacks" not in json.loads(rec.requests[0].content)


# ── OpenAI-compatible ──────────────────────────────────────────────────────────────────────────
def _openai():
    s = _settings(llm_provider="openai", llm_model="gpt-test", llm_base_url="https://llm.example.test/v1")
    return OpenAICompatibleProvider(s, "gpt-test")


@respx.mock
def test_openai_compatible_request_and_tool_parsing(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    route = respx.post("https://llm.example.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(503, json={"error": "busy"}),
            httpx.Response(
                200,
                json={
                    "model": "gpt-test",
                    "usage": {"prompt_tokens": 50, "completion_tokens": 5},
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {"name": "get_profile", "arguments": "{}"},
                                    }
                                ],
                            },
                        }
                    ],
                },
            ),
        ]
    )
    res = _openai().complete(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[ToolSpec("get_profile", "p", {"type": "object", "properties": {}})],
    )
    assert route.call_count == 2  # 503 retried
    body = json.loads(route.calls[1].request.content)
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["function"]["name"] == "get_profile"
    assert route.calls[1].request.headers["authorization"] == "Bearer test-key-not-real"
    assert res.tool_calls == [{"id": "c1", "name": "get_profile", "arguments": {}}]
    assert res.cost_usd is None  # unknown price is reported as unknown, never guessed


@respx.mock
def test_openai_compatible_schema_and_refusal():
    route = respx.post("https://llm.example.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": false}'}}], "usage": {}}),
            httpx.Response(200, json={"choices": [{"message": {"content": None, "refusal": "no"}}], "usage": {}}),
        ]
    )
    llm = _openai()
    assert llm.complete(system="s", messages=[{"role": "user", "content": "x"}], json_schema=SCHEMA).data == {
        "ok": False
    }
    assert json.loads(route.calls[0].request.content)["response_format"]["json_schema"]["strict"] is True
    with pytest.raises(LLMRefusal):
        llm.complete(system="s", messages=[{"role": "user", "content": "x"}])


# ── Gmail ──────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def gmail_configured(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "client-id.test")
    monkeypatch.setattr(s, "google_client_secret", "client-secret-not-real")
    monkeypatch.setattr(s, "public_app_url", "https://jobpilot.example.test")
    return s


def test_gmail_authorization_url(gmail_configured):
    from urllib.parse import parse_qs, urlsplit

    from app.integrations import gmail

    q = parse_qs(urlsplit(gmail.authorization_url("state123")).query)
    assert q["redirect_uri"] == ["https://jobpilot.example.test/api/v1/integrations/gmail/callback"]
    assert q["access_type"] == ["offline"] and q["state"] == ["state123"]
    scopes = q["scope"][0].split()
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
    assert "https://www.googleapis.com/auth/gmail.compose" in scopes
    assert not any(s.endswith("gmail.send") or s.endswith("mail.google.com/") for s in scopes)  # never send scope


@respx.mock
def test_gmail_token_exchange_refresh_list_and_draft(gmail_configured):
    from app.integrations import gmail

    respx.post(gmail.TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "at2", "expires_in": 3600}),
        ]
    )
    tokens = gmail.exchange_code("code123")
    assert tokens["refresh_token"] == "rt1"
    sealed = gmail.seal(tokens)
    assert "rt1" not in sealed and gmail.unseal(sealed)["refresh_token"] == "rt1"

    tokens["expires_at"] = 0  # force refresh
    respx.get(f"{gmail.API}/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]}))
    respx.get(f"{gmail.API}/messages/m1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "snippet": "We'd like to schedule an interview",
                "internalDate": "1790000000000",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Talent <jobs@acme.test>"},
                        {"name": "Subject", "value": "Next steps"},
                    ]
                },
            },
        )
    )
    drafts = respx.post(f"{gmail.API}/drafts").mock(return_value=httpx.Response(200, json={"id": "d1"}))
    client = gmail.GmailClient(tokens)
    msgs = client.recent_job_messages()
    assert client.refreshed and msgs[0]["subject"] == "Next steps"
    assert respx.calls[-1].request.headers["authorization"] == "Bearer at2"
    assert client.create_draft("hr@acme.test", "Thanks", "Hello") == "d1"
    import base64

    raw = base64.urlsafe_b64decode(json.loads(drafts.calls[0].request.content)["message"]["raw"]).decode()
    assert "To: hr@acme.test" in raw and "Subject: Thanks" in raw


@respx.mock
def test_gmail_revoked_access_is_reported(gmail_configured):
    from app.integrations import gmail

    respx.get(f"{gmail.API}/messages").mock(return_value=httpx.Response(401, json={}))
    client = gmail.GmailClient({"access_token": "x", "expires_at": time.time() + 999})
    with pytest.raises(gmail.GmailError, match="revoked"):
        client.recent_job_messages()


@respx.mock
def test_gmail_sync_classifies_links_and_acknowledges(gmail_configured, user_api, db, monkeypatch):
    """Full sync: only job mail is stored, replies link to applications, confirmations acknowledge."""
    from sqlalchemy import select

    from app.integrations import gmail
    from app.integrations.sources import providers
    from app.models import Application, Communication, Integration, User
    from app.services.communications import sync_gmail
    from tests.helpers import FakeAdapter

    monkeypatch.setitem(providers.ADAPTERS, "greenhouse", FakeAdapter())
    user_api.post("/api/v1/job-sources", json={"provider": "greenhouse", "identifier": "acme"})
    job_id = user_api.get("/api/v1/jobs").json()["items"][0]["id"]
    app_id = user_api.post("/api/v1/applications", json={"job_id": job_id}).json()["id"]
    app = db.get(Application, app_id)
    app.status, app.submission_state = "approved", "attempted"  # as if the user started an assisted submission
    db.commit()

    user = db.scalar(select(User))
    integ = Integration(
        user_id=user.id,
        provider="gmail",
        status="connected",
        credentials_encrypted=gmail.seal({"access_token": "at", "expires_at": time.time() + 999}),
    )
    db.add(integ)
    db.commit()

    def msg(mid, subject, snippet, sender):
        return {
            "id": mid,
            "threadId": mid,
            "snippet": snippet,
            "internalDate": "1790000000000",
            "payload": {"headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]},
        }

    messages = {
        "a": msg(
            "a",
            "Thank you for applying to Acme Payments",
            "We have received your application",
            "no-reply@acmepayments.test",
        ),
        "b": msg("b", "Weekend sale", "50% off shoes", "deals@shop.test"),
        "c": msg("c", "Interview with Acme Payments", "We'd like to schedule an interview", "talent@acmepayments.test"),
    }
    respx.get(f"{gmail.API}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": k} for k in messages]})
    )
    for k, v in messages.items():
        respx.get(f"{gmail.API}/messages/{k}").mock(return_value=httpx.Response(200, json=v))

    result = sync_gmail(db, user, integ)
    assert result == {"scanned": 3, "stored": 2, "acknowledged": 1}
    stored = {c.external_id: c for c in db.scalars(select(Communication))}
    assert set(stored) == {"a", "c"}  # the promotional email is never stored
    assert stored["c"].category == "interview_invitation" and stored["c"].application_id == app_id
    db.refresh(app)
    assert app.status == "submitted" and app.submission_state == "acknowledged"
    assert app.submission_reference == "email:a"
    assert sync_gmail(db, user, integ)["stored"] == 0  # idempotent


# ── WhatsApp ───────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def whatsapp_configured(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_access_token", "wa-token-not-real")
    monkeypatch.setattr(s, "whatsapp_phone_number_id", "1234567890")
    return s


@respx.mock
def test_whatsapp_send_request_and_errors(whatsapp_configured):
    from app.integrations import whatsapp

    route = respx.post(f"{whatsapp.GRAPH}/1234567890/messages").mock(
        side_effect=[
            httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}),
            httpx.Response(400, json={"error": {"message": "Re-engagement message"}}),
        ]
    )
    assert whatsapp.send_text("+919876543210", "Hello") == "wamid.1"
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "messaging_product": "whatsapp",
        "to": "919876543210",
        "type": "text",
        "text": {"body": "Hello", "preview_url": False},
    }
    assert route.calls[0].request.headers["authorization"] == "Bearer wa-token-not-real"
    with pytest.raises(whatsapp.WhatsAppError, match="Re-engagement") as e:
        whatsapp.send_text("+919876543210", "Hello")
    assert "wa-token" not in str(e.value)


@respx.mock
def test_worker_delivery_statuses(whatsapp_configured, user_api, db):
    from sqlalchemy import select

    from app.integrations import whatsapp
    from app.models import Notification, User
    from app.services import audit
    from app.worker.main import deliver_notifications

    user = db.scalar(select(User))
    respx.post(f"{whatsapp.GRAPH}/1234567890/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.9"}]})
    )
    audit.notify(db, user_id=user.id, kind="approval_requested", title="Approve")
    db.commit()
    deliver_notifications(db)
    n = db.scalar(select(Notification))
    assert n.deliveries["whatsapp"]["status"] == "disabled"  # user hasn't opted in

    user.settings.notify_whatsapp, user.settings.whatsapp_number = True, "+919876543210"
    audit.notify(db, user_id=user.id, kind="application_failed", title="Failed")
    db.commit()
    deliver_notifications(db)
    latest = db.scalar(select(Notification).where(Notification.kind == "application_failed"))
    assert latest.deliveries["whatsapp"] == {"status": "sent", "id": "wamid.9"}
    deliver_notifications(db)
    assert respx.calls.call_count == 1  # never re-sent
