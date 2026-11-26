"""Gmail integration via Google OAuth 2.0 (authorization-code flow with offline access).

Scopes are minimal: read message metadata/snippets and create drafts. JobPilot never sends email
itself — drafts land in the user's Gmail Drafts folder for them to review and send.
"""

from __future__ import annotations

import base64
import json
import time
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.security.core import decrypt_secret, encrypt_secret

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint URL, not a secret
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "openid",
    "email",
]
JOB_QUERY = (
    "newer_than:60d -category:promotions (application OR applying OR interview OR recruiter OR offer OR "
    "assessment OR candidate OR position OR role OR hiring)"
)


class GmailError(RuntimeError):
    pass


def redirect_uri() -> str:
    return f"{get_settings().public_app_url}/api/v1/integrations/gmail/callback"


def authorization_url(state: str) -> str:
    s = get_settings()
    if not s.gmail_configured:
        raise GmailError("Gmail requires JOBPILOT_GOOGLE_CLIENT_ID and JOBPILOT_GOOGLE_CLIENT_SECRET.")
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": s.google_client_id,
                "redirect_uri": redirect_uri(),
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "include_granted_scopes": "true",
            }
        )
    )


def exchange_code(code: str) -> dict[str, Any]:
    s = get_settings()
    r = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if r.status_code != 200:
        raise GmailError("Google rejected the authorization code.")
    tok = r.json()
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    return tok


def seal(tokens: dict[str, Any]) -> str:
    return encrypt_secret(json.dumps(tokens))


def unseal(blob: str | None) -> dict[str, Any]:
    raw = decrypt_secret(blob) if blob else None
    if not raw:
        raise GmailError("Gmail credentials are missing or unreadable. Reconnect Gmail.")
    return json.loads(raw)


class GmailClient:
    def __init__(self, tokens: dict[str, Any]) -> None:
        self.tokens = tokens
        self.refreshed = False

    def _token(self) -> str:
        if time.time() >= self.tokens.get("expires_at", 0):
            s = get_settings()
            r = httpx.post(
                TOKEN_URL,
                data={
                    "client_id": s.google_client_id,
                    "client_secret": s.google_client_secret,
                    "refresh_token": self.tokens.get("refresh_token"),
                    "grant_type": "refresh_token",
                },
                timeout=20,
            )
            if r.status_code != 200:
                raise GmailError("Gmail access expired or was revoked. Reconnect Gmail.")
            new = r.json()
            self.tokens.update(
                access_token=new["access_token"], expires_at=time.time() + int(new.get("expires_in", 3600)) - 60
            )
            self.refreshed = True
        return self.tokens["access_token"]

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        r = httpx.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {self._token()}"}, timeout=20)
        if r.status_code == 401:
            raise GmailError("Gmail access was revoked. Reconnect Gmail.")
        if r.status_code >= 400:
            raise GmailError(f"Gmail API error {r.status_code}.")
        return r.json()

    def profile_email(self) -> str | None:
        return self._get("/profile").get("emailAddress")

    def recent_job_messages(self, limit: int = 50) -> list[dict[str, Any]]:
        listing = self._get("/messages", {"q": JOB_QUERY, "maxResults": limit})
        out = []
        for m in listing.get("messages", []):
            msg = self._get(
                f"/messages/{m['id']}", {"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]}
            )
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            out.append(
                {
                    "id": msg["id"],
                    "thread_id": msg.get("threadId"),
                    "snippet": msg.get("snippet", ""),
                    "from": headers.get("from", ""),
                    "to": headers.get("to", ""),
                    "subject": headers.get("subject", ""),
                    "internal_date": int(msg.get("internalDate", 0)),
                }
            )
        return out

    def create_draft(self, to: str, subject: str, body: str) -> str:
        em = EmailMessage()
        em["To"], em["Subject"] = to, subject
        em.set_content(body)
        raw = base64.urlsafe_b64encode(em.as_bytes()).decode()
        r = httpx.post(
            f"{API}/drafts",
            json={"message": {"raw": raw}},
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=20,
        )
        if r.status_code >= 400:
            raise GmailError(f"Could not create the Gmail draft ({r.status_code}).")
        return r.json()["id"]
