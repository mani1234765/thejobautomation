from tests.conftest import make_client, register


def test_register_login_logout(api):
    me = register(api)
    assert me["email"] == "asha@example.com" and me["full_name"] == "Asha Verma"
    assert api.get("/api/v1/auth/me").status_code == 200
    assert api.post("/api/v1/auth/logout").status_code == 204
    api.c.cookies.clear()
    assert api.get("/api/v1/auth/me").status_code == 401
    r = api.post("/api/v1/auth/login", json={"email": "ASHA@example.com", "password": "correct-horse-42"})
    assert r.status_code == 200


def test_session_cookie_is_httponly(api):
    r = api.post("/api/v1/auth/register", json={"email": "a@x.io", "password": "correct-horse-42", "full_name": "A"})
    cookies = r.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("jp_session="))
    assert "HttpOnly" in session and "SameSite=lax" in session
    assert "HttpOnly" not in next(c for c in cookies if c.startswith("jp_csrf="))


def test_weak_password_and_duplicate_email(api):
    r = api.post("/api/v1/auth/register", json={"email": "b@x.io", "password": "aaaaaaaaaa", "full_name": "B"})
    assert r.status_code == 422
    register(api, email="c@x.io")
    r = api.post("/api/v1/auth/register", json={"email": "c@x.io", "password": "correct-horse-42", "full_name": "C"})
    assert r.status_code == 409


def test_wrong_password_is_generic(api):
    register(api)
    r1 = api.post("/api/v1/auth/login", json={"email": "asha@example.com", "password": "wrong-password-1"})
    r2 = api.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["message"] == r2.json()["error"]["message"]


def test_csrf_required_for_cookie_mutations(user_api):
    r = user_api.c.put("/api/v1/profile", json={"full_name": "X"})  # no CSRF header
    assert r.status_code == 403
    r = user_api.c.put("/api/v1/profile", json={"full_name": "X"}, headers={"x-jobpilot-csrf": "forged"})
    assert r.status_code == 403
    assert user_api.put("/api/v1/profile", json={"full_name": "X"}).status_code == 200


def test_login_rate_limited(api):
    register(api)
    codes = [
        api.post("/api/v1/auth/login", json={"email": "asha@example.com", "password": "nope-nope-1"}).status_code
        for _ in range(12)
    ]
    assert 429 in codes


def test_user_data_isolation(user_api):
    r = user_api.post("/api/v1/profile/experiences", json={"category": "professional", "employer": "Acme"})
    exp_id = r.json()["id"]
    other = make_client()
    register(other, email="mallory@example.com")
    assert (
        other.put(
            f"/api/v1/profile/experiences/{exp_id}", json={"category": "professional", "employer": "Hacked"}
        ).status_code
        == 404
    )
    assert other.delete(f"/api/v1/profile/experiences/{exp_id}").status_code == 404
    assert other.get("/api/v1/profile").json()["experiences"] == []


def test_api_token_scopes_and_web_only_routes(user_api):
    r = user_api.post("/api/v1/auth/tokens", json={"name": "mcp", "scopes": ["read"]})
    token = r.json()["token"]
    assert token.startswith("jp_")
    from fastapi.testclient import TestClient

    from app.main import create_app

    bearer = TestClient(create_app(), headers={"Authorization": f"Bearer {token}"})
    assert bearer.get("/api/v1/profile").status_code == 200
    # No CSRF needed for bearer tokens, but scopes are enforced:
    assert bearer.post("/api/v1/job-sources", json={"provider": "lever", "identifier": "x"}).status_code == 403
    # Web-only routes are never reachable with a token, whatever its scopes.
    assert bearer.put("/api/v1/profile", json={"full_name": "T"}).status_code == 403
    assert bearer.post("/api/v1/applications/x/submission", json={"action": "start"}).status_code == 403
    # Revocation takes effect immediately.
    tid = user_api.get("/api/v1/auth/tokens").json()[0]["id"]
    user_api.delete(f"/api/v1/auth/tokens/{tid}")
    assert bearer.get("/api/v1/profile").status_code == 401


def test_logout_all_invalidates_sessions(user_api):
    stolen = dict(user_api.c.cookies)
    assert user_api.post("/api/v1/auth/logout-all").status_code == 204
    other = make_client()
    for k, v in stolen.items():
        other.c.cookies.set(k, v)
    assert other.get("/api/v1/auth/me").status_code == 401


def test_upload_validation(user_api):
    r = user_api.post("/api/v1/resumes", files={"file": ("cv.pdf", b"MZ\x90\x00not a pdf", "application/pdf")})
    assert r.status_code == 400
    r = user_api.post("/api/v1/resumes", files={"file": ("cv.docx", b"%PDF-1.4 fake", "application/pdf")})
    assert r.status_code == 400 and "extension" in r.json()["error"]["message"]
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<x/>")
        zf.writestr("word/vbaProject.bin", "macro")
    r = user_api.post("/api/v1/resumes", files={"file": ("cv.docx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 400 and "Macro" in r.json()["error"]["message"]


def test_settings_cannot_disable_approval(user_api):
    s = user_api.get("/api/v1/settings").json()
    s["require_approval"] = False
    assert user_api.put("/api/v1/settings", json=s).status_code == 422


def test_security_headers_and_no_stack_traces(user_api):
    r = user_api.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    r = user_api.get("/api/v1/applications/does-not-exist")
    assert r.status_code == 404 and "Traceback" not in r.text


def test_export_and_delete_account(user_api, db):
    data = user_api.get("/api/v1/export").json()
    assert data["account"]["email"] == "asha@example.com"
    assert "password_hash" not in str(data)
    assert (
        user_api.post(
            "/api/v1/account/delete", json={"confirm_email": "wrong@x.io", "password": "correct-horse-42"}
        ).status_code
        == 400
    )
    assert (
        user_api.post(
            "/api/v1/account/delete", json={"confirm_email": "asha@example.com", "password": "wrong-pass-123"}
        ).status_code
        == 400
    )
    assert (
        user_api.post(
            "/api/v1/account/delete", json={"confirm_email": "asha@example.com", "password": "correct-horse-42"}
        ).status_code
        == 204
    )
    from sqlalchemy import func, select

    from app.models import AuditEvent, User

    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_ai_model_override_requires_operator_allowlist(user_api, monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "llm_provider", "anthropic")
    monkeypatch.setattr(s, "llm_model", "claude-opus-5-5")
    monkeypatch.setattr(s, "llm_api_key", "test-key-not-real")
    body = user_api.get("/api/v1/settings").json()
    body["ai_model_override"] = "some-unvetted-model"
    assert user_api.put("/api/v1/settings", json=body).status_code == 400
    monkeypatch.setattr(s, "llm_allowed_models", "claude-sonnet-5-5")
    body["ai_model_override"] = "claude-sonnet-5-5"
    assert user_api.put("/api/v1/settings", json=body).status_code == 200


def test_logout_revokes_the_session_server_side(user_api):
    stolen = dict(user_api.c.cookies)
    assert user_api.post("/api/v1/auth/logout").status_code == 204
    replay = make_client()
    for k, v in stolen.items():
        replay.c.cookies.set(k, v)
    r = replay.get("/api/v1/auth/me")
    assert r.status_code == 401 and "signed out" in r.json()["error"]["message"]


def test_oversized_upload_rejected_while_streaming(user_api, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    r = user_api.post(
        "/api/v1/resumes", files={"file": ("cv.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10), "application/pdf")}
    )
    assert r.status_code == 413


def test_production_rejects_malformed_encryption_key():
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="Fernet"):
        Settings(
            env="production",
            secret_key="Zq7xW2pL9vR4tY8uK3mN6bH1cJ5dF0gS-eA_x9",
            encryption_key="not-a-key",
            cookie_secure=True,
            redis_url="redis://r",
        )


def test_readiness_reports_dependencies(api, monkeypatch):
    r = api.get("/api/v1/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")
    r = api.get("/api/v1/health/ready")
    assert r.status_code == 503 and r.json()["checks"]["rate_limiter"] == "redis:unavailable"


def test_production_rejects_placeholder_secret_keys():
    import pytest
    from cryptography.fernet import Fernet
    from pydantic import ValidationError

    from app.config import Settings

    ok = dict(
        env="production", encryption_key=Fernet.generate_key().decode(), cookie_secure=True, redis_url="redis://r"
    )
    for weak in (
        "change-me-to-a-long-random-string-at-least-32-chars",
        "a" * 40,
        "dev-insecure-secret-key-change-me-0000000000",
    ):
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            Settings(secret_key=weak, **ok)
    Settings(secret_key="Zq7xW2pL9vR4tY8uK3mN6bH1cJ5dF0gS-eA_" + "x9", **ok)
