from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, web_only
from app.config import get_settings
from app.db import utcnow
from app.integrations import gmail
from app.integrations.whatsapp import WhatsAppError, send_text
from app.models import Application, Communication, Integration
from app.services import audit
from app.services import communications as svc

router = APIRouter(tags=["communications"])


class CommunicationOut(BaseModel):
    id: str
    application_id: str | None
    channel: str
    direction: str
    sender: str | None
    recipient: str | None
    subject: str | None
    snippet: str | None
    body: str | None
    category: str
    classified_by: str
    occurred_at: datetime
    company: str | None = None
    job_title: str | None = None


@router.get("/communications", response_model=list[CommunicationOut])
def list_communications(
    db: DB, p: CurrentPrincipal, category: str | None = None, application_id: str | None = None
) -> list[CommunicationOut]:
    q = select(Communication).where(Communication.user_id == p.id).order_by(Communication.occurred_at.desc()).limit(200)
    if category:
        q = q.where(Communication.category == category)
    if application_id:
        q = q.where(Communication.application_id == application_id)
    out = []
    for c in db.scalars(q):
        item = CommunicationOut.model_validate(c, from_attributes=True)
        if c.application_id:
            app = db.get(Application, c.application_id)
            if app:
                item.company, item.job_title = app.job.company.name, app.job.title
        out.append(item)
    return out


class DraftIn(BaseModel):
    kind: Literal[
        "follow_up", "thank_you", "referral_request", "recruiter_message", "linkedin_connection", "linkedin_follow_up"
    ]
    application_id: str | None = None
    recipient_name: str | None = Field(default=None, max_length=120)
    context: str | None = Field(default=None, max_length=2000)


@router.post("/communications/draft")
def draft(body: DraftIn, db: DB, p: Principal = Depends(web_only)) -> dict:
    app = get_owned(db, Application, body.application_id, p.id) if body.application_id else None
    return svc.draft_message(db, p.user, body.kind, app, body.recipient_name, body.context)


class LogIn(BaseModel):
    channel: Literal["email", "linkedin", "phone", "other"]
    direction: Literal["inbound", "outbound"]
    application_id: str | None = None
    subject: str | None = Field(default=None, max_length=500)
    body: str = Field(min_length=1, max_length=10_000)
    counterpart: str | None = Field(default=None, max_length=300)
    category: Literal[
        "recruiter_outreach",
        "follow_up",
        "referral_request",
        "interview_invitation",
        "rejection",
        "offer",
        "assessment",
        "note",
    ] = "note"


@router.post("/communications", response_model=CommunicationOut, status_code=201)
def log_communication(body: LogIn, db: DB, p: Principal = Depends(web_only)) -> Communication:
    app = get_owned(db, Application, body.application_id, p.id) if body.application_id else None
    return svc.log_manual(
        db,
        p.user,
        channel=body.channel,
        direction=body.direction,
        app=app,
        subject=body.subject,
        body=body.body,
        counterpart=body.counterpart,
        category=body.category,
    )


# ── Integrations ───────────────────────────────────────────────────────────────────────────────
def _integration(db: DB, user_id: str, provider: str) -> Integration:
    integ = db.scalar(select(Integration).where(Integration.user_id == user_id, Integration.provider == provider))
    if integ is None:
        integ = Integration(user_id=user_id, provider=provider, status="disconnected")
        db.add(integ)
        db.flush()
    return integ


@router.get("/integrations")
def list_integrations(db: DB, p: CurrentPrincipal) -> list[dict]:
    s = get_settings()
    rows = {i.provider: i for i in db.scalars(select(Integration).where(Integration.user_id == p.id))}
    g, w = rows.get("gmail"), rows.get("whatsapp")
    return [
        {
            "provider": "gmail",
            "name": "Gmail",
            "available": s.gmail_configured,
            "setup_hint": None
            if s.gmail_configured
            else "Server needs Google OAuth credentials (JOBPILOT_GOOGLE_CLIENT_ID / _SECRET).",
            "status": g.status if g else "disconnected",
            "account": g.account_label if g else None,
            "last_synced_at": g.last_synced_at if g else None,
            "last_error": g.last_error if g else None,
            "scopes": ["Read job-related message metadata", "Create drafts (never sends)"],
        },
        {
            "provider": "whatsapp",
            "name": "WhatsApp",
            "available": s.whatsapp_configured,
            "setup_hint": None if s.whatsapp_configured else "Server needs WhatsApp Business Cloud API credentials.",
            "status": "connected" if (w and w.status == "connected") else "disconnected",
            "account": p.user.settings.whatsapp_number,
            "last_synced_at": None,
            "last_error": w.last_error if w else None,
            "scopes": ["Send notifications you enable to your number"],
        },
        {
            "provider": "ai",
            "name": "AI provider",
            "available": s.ai_configured,
            "setup_hint": None
            if s.ai_configured
            else "Set JOBPILOT_LLM_PROVIDER, JOBPILOT_LLM_MODEL and JOBPILOT_LLM_API_KEY on the server.",
            "status": "connected" if s.ai_configured else "disconnected",
            "account": f"{s.llm_provider} · {s.llm_model}" if s.ai_configured else None,
            "last_synced_at": None,
            "last_error": None,
            "scopes": [],
        },
    ]


@router.post("/integrations/gmail/connect")
def gmail_connect(db: DB, p: Principal = Depends(web_only)) -> dict:
    integ = _integration(db, p.id, "gmail")
    integ.oauth_state = secrets.token_urlsafe(24)
    db.commit()
    try:
        return {"authorization_url": gmail.authorization_url(integ.oauth_state)}
    except gmail.GmailError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/integrations/gmail/callback")
def gmail_callback(
    db: DB, p: CurrentPrincipal, state: str, code: str | None = None, error: str | None = None
) -> RedirectResponse:
    base = f"{get_settings().public_app_url}/integrations"
    integ = _integration(db, p.id, "gmail")
    if not integ.oauth_state or not secrets.compare_digest(integ.oauth_state, state):
        return RedirectResponse(f"{base}?gmail=invalid_state")
    integ.oauth_state = None
    if error or not code:
        db.commit()
        return RedirectResponse(f"{base}?gmail=denied")
    try:
        tokens = gmail.exchange_code(code)
        integ.credentials_encrypted = gmail.seal(tokens)
        integ.account_label = gmail.GmailClient(tokens).profile_email()
    except gmail.GmailError as exc:
        integ.status, integ.last_error = "error", str(exc)
        db.commit()
        return RedirectResponse(f"{base}?gmail=error")
    integ.status, integ.connected_at, integ.scopes, integ.last_error = "connected", utcnow(), gmail.SCOPES, None
    audit.record(
        db,
        user_id=p.id,
        actor="user",
        action="integration.connected",
        entity_type="integration",
        entity_id=integ.id,
        meta={"provider": "gmail"},
    )
    db.commit()
    return RedirectResponse(f"{base}?gmail=connected")


@router.post("/integrations/gmail/sync")
def gmail_sync(db: DB, p: Principal = Depends(web_only)) -> dict:
    integ = _integration(db, p.id, "gmail")
    if integ.status not in ("connected", "error") or not integ.credentials_encrypted:
        raise HTTPException(400, "Connect Gmail first.")
    try:
        return svc.sync_gmail(db, p.user, integ)
    except gmail.GmailError as exc:
        raise HTTPException(502, str(exc)) from exc


class GmailDraftIn(BaseModel):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1, max_length=20_000)
    application_id: str | None = None


@router.post("/integrations/gmail/drafts")
def gmail_draft(body: GmailDraftIn, db: DB, p: Principal = Depends(web_only)) -> dict:
    integ = _integration(db, p.id, "gmail")
    if integ.status != "connected":
        raise HTTPException(400, "Connect Gmail first.")
    client = gmail.GmailClient(gmail.unseal(integ.credentials_encrypted))
    try:
        draft_id = client.create_draft(body.to, body.subject, body.body)
    except gmail.GmailError as exc:
        raise HTTPException(502, str(exc)) from exc
    if client.refreshed:
        integ.credentials_encrypted = gmail.seal(client.tokens)
    app = get_owned(db, Application, body.application_id, p.id) if body.application_id else None
    svc.log_manual(
        db,
        p.user,
        channel="email",
        direction="draft",
        app=app,
        subject=body.subject,
        body=body.body,
        counterpart=body.to,
        category="follow_up",
    )
    audit.record(db, user_id=p.id, actor="user", action="gmail.draft_created")
    db.commit()
    return {"draft_id": draft_id}


@router.delete("/integrations/{provider}", status_code=204)
def disconnect(provider: Literal["gmail", "whatsapp"], db: DB, p: Principal = Depends(web_only)) -> None:
    integ = _integration(db, p.id, provider)
    integ.status, integ.credentials_encrypted, integ.account_label = "disconnected", None, None
    audit.record(db, user_id=p.id, actor="user", action="integration.disconnected", meta={"provider": provider})
    db.commit()


@router.post("/integrations/whatsapp/test")
def whatsapp_test(db: DB, p: Principal = Depends(web_only)) -> dict:
    number = p.user.settings.whatsapp_number
    if not number:
        raise HTTPException(400, "Add your WhatsApp number in Settings → Notifications first.")
    integ = _integration(db, p.id, "whatsapp")
    try:
        msg_id = send_text(number, "JobPilot: WhatsApp notifications are working.")
    except WhatsAppError as exc:
        integ.status, integ.last_error = "error", str(exc)
        db.commit()
        raise HTTPException(502, str(exc)) from exc
    integ.status, integ.last_error, integ.connected_at = "connected", None, utcnow()
    db.commit()
    return {"message_id": msg_id}
