"""Tests for the integration verifier itself, so it works the first time real credentials are added.

The provider here is a test double: these tests prove the verifier's logic and the product feature paths
it drives — they are NOT live verification (that requires credentials, see the verifier's BLOCKED rows).
"""

import time

import httpx
import respx

from app.ai.provider import LLMResult
from app.ops import verify_integrations as v


class ShapeAwareLLM:
    """Answers each request according to what it asks for (schema, tools, prompt)."""

    name, model = "anthropic", "claude-opus-5-5"

    def complete(self, *, system, messages, json_schema=None, tools=None, max_tokens=None):
        last = messages[-1]
        text, data, calls = "", None, []
        props = (json_schema or {}).get("properties", {})
        if tools and last["role"] == "user":
            calls = [{"id": "t1", "name": tools[0].name, "arguments": {}}]
        elif "sum" in props:
            data = {"sum": 5}
        elif "changes" in props:
            pid = next(line.split("]")[0].strip("[") for line in last["content"].splitlines() if line.startswith("[b"))
            data = {
                "summary_of_fit": "Good fit.",
                "changes": [
                    {
                        "paragraph_id": pid,
                        "proposed": "Built REST APIs in Python and PostgreSQL used by 40 internal teams.",
                        "rationale": "verb",
                    }
                ],
            }
        elif "answers" in props:
            data = {
                "answers": [
                    {"question": "Why do you want this role?", "answer": "Python APIs.", "known": True},
                    {"question": "What is your passport number?", "answer": "", "known": False},
                ]
            }
        elif "questions" in props:
            data = {
                "focus_areas": ["PostgreSQL"],
                "questions": [{"category": "technical", "question": "Index design?", "rationale": "JD"}],
            }
        elif "scores" in props:
            data = {
                "scores": dict.fromkeys(
                    ("relevance", "clarity", "structure", "technical_accuracy", "communication"), 4
                ),
                "strengths": ["clear"],
                "improvements": ["more metrics"],
                "star": dict.fromkeys(("situation", "task", "action", "result"), True),
                "improved_answer": "…",
            }
        elif "Reply with exactly: OK" in system:
            text = "OK"
        else:
            text = (
                "Dear team, …"
                if "cover letter" in system.lower() or "cover letter" in last.get("content", "")
                else "Backend Engineer fits best."
            )
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=100,
            output_tokens=20,
            tool_calls=calls,
            data=data,
            raw_assistant=[{"type": "text", "text": text}],
        )


def _configure_ai(monkeypatch):
    from app.ai import provider, service
    from app.config import get_settings

    s = get_settings()
    for k, val in (
        ("llm_provider", "anthropic"),
        ("llm_model", "claude-opus-5-5"),
        ("llm_api_key", "test-key-not-real"),
    ):
        monkeypatch.setattr(s, k, val)
    monkeypatch.setattr(provider, "build_provider", lambda *_a, **_k: ShapeAwareLLM())
    monkeypatch.setattr(service, "provider_for", lambda user: ShapeAwareLLM())


def test_blocked_without_credentials(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "redis_url", None)
    rows = {
        c.name: c for c in (v.check_ai_config(), v.check_gmail_config(), v.check_whatsapp_config(), v.check_redis())
    }
    assert all(c.status == v.BLOCKED for c in rows.values())
    assert any("JOBPILOT_LLM_API_KEY" in c.detail for c in rows.values())
    assert rows["gmail.config"].setup and rows["whatsapp.config"].setup


def test_ai_checks_pass_with_a_working_provider(monkeypatch):
    _configure_ai(monkeypatch)
    assert v.check_ai_config().status == v.PASS
    live = {c.name: c for c in v._ai_live_checks()}
    assert {c.status for c in live.values()} == {v.PASS}, live
    feats = {c.name: c for c in v._ai_feature_checks()}
    assert set(feats) == {
        "ai.feature.resume_rewording",
        "ai.feature.cover_letter",
        "ai.feature.interview_feedback",
        "ai.feature.assistant",
    }
    assert all(c.status == v.PASS for c in feats.values()), {k: c.detail for k, c in feats.items()}
    assert "unknown answer left blank=True" in feats["ai.feature.cover_letter"].detail


def test_ai_checks_fail_honestly_when_provider_misbehaves(monkeypatch):
    _configure_ai(monkeypatch)

    class Wrong(ShapeAwareLLM):
        def complete(self, **kw):
            r = super().complete(**kw)
            r.text, r.data, r.tool_calls = "nope", {"sum": 4}, []
            return r

    from app.ai import provider

    monkeypatch.setattr(provider, "build_provider", lambda *_a, **_k: Wrong())
    assert {c.status for c in v._ai_live_checks()} == {v.FAIL}


@respx.mock
def test_whatsapp_live_token_check_and_send_gating(monkeypatch):
    from app.config import get_settings
    from app.integrations.whatsapp import GRAPH

    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_access_token", "wa-not-real")
    monkeypatch.setattr(s, "whatsapp_phone_number_id", "555")
    respx.get(f"{GRAPH}/555").mock(
        return_value=httpx.Response(200, json={"display_phone_number": "+1 555", "verified_name": "Test"})
    )
    rows = {c.name: c for c in v.check_whatsapp_live(None)}
    assert rows["whatsapp.token"].status == v.PASS
    assert rows["whatsapp.send"].status == v.BLOCKED  # nothing is sent without the explicit flag
    respx.get(f"{GRAPH}/555").mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid OAuth access token"}})
    )
    assert v.check_whatsapp_live(None)[0].status == v.FAIL


@respx.mock
def test_gmail_live_reads_without_storing(monkeypatch, user_api, db):
    from sqlalchemy import func, select

    from app.config import get_settings
    from app.integrations import gmail
    from app.models import Communication, Integration, User

    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "cid")
    monkeypatch.setattr(s, "google_client_secret", "csecret")
    user = db.scalar(select(User))
    db.add(
        Integration(
            user_id=user.id,
            provider="gmail",
            status="connected",
            credentials_encrypted=gmail.seal({"access_token": "at", "expires_at": time.time() + 999}),
        )
    )
    db.commit()
    respx.get(f"{gmail.API}/profile").mock(return_value=httpx.Response(200, json={"emailAddress": "asha@example.com"}))
    respx.get(f"{gmail.API}/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "1"}]}))
    respx.get(f"{gmail.API}/messages/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "1",
                "snippet": "Unfortunately we are not moving forward",
                "internalDate": "1",
                "payload": {"headers": [{"name": "Subject", "value": "Your application"}]},
            },
        )
    )
    rows = {c.name: c for c in v.check_gmail_live("asha@example.com", None)}
    assert rows["gmail.oauth"].status == v.PASS and rows["gmail.read_and_classify"].status == v.PASS
    assert "rejection" in rows["gmail.read_and_classify"].detail
    assert rows["gmail.draft"].status == v.BLOCKED  # no draft without the explicit flag
    assert db.scalar(select(func.count()).select_from(Communication)) == 0  # verification never stores mail
