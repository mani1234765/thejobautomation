from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import service as ai
from app.ai.guards import untrusted
from app.ai.provider import AIUnavailable, LLMError
from app.db import utcnow
from app.domain import email_classifier
from app.domain.job_parsing import normalize_company
from app.integrations.gmail import GmailClient, GmailError, seal, unseal
from app.models import Application, Communication, Integration, User
from app.services import applications as app_svc
from app.services import audit
from app.services.applications import _profile_context

DRAFT_KINDS = {
    "follow_up": "a polite follow-up email about the status of a submitted application",
    "thank_you": "a thank-you email after an interview",
    "referral_request": "a short, respectful referral request to someone who works at the company",
    "recruiter_message": "a concise message to a recruiter expressing interest in the role",
    "linkedin_connection": "a LinkedIn connection request note (max 280 characters)",
    "linkedin_follow_up": "a short LinkedIn follow-up message",
}


def _match_application(db: Session, user_id: str, subject: str, sender: str, snippet: str) -> Application | None:
    apps = db.scalars(select(Application).where(Application.user_id == user_id)).all()
    domain = email_classifier.sender_domain(sender) or ""
    blob = f"{subject} {snippet} {sender}".lower()
    for a in apps:
        name = normalize_company(a.job.company.name)
        if name and (name in blob or name.replace(" ", "") in domain):
            return a
    return None


def sync_gmail(db: Session, user: User, integ: Integration) -> dict[str, int]:
    client = GmailClient(unseal(integ.credentials_encrypted))
    try:
        messages = client.recent_job_messages()
    except GmailError as exc:
        integ.status, integ.last_error = "error", str(exc)
        db.commit()
        raise
    stored = acked = 0
    for m in messages:
        c = email_classifier.classify(m["subject"], m["snippet"], m["from"])
        if c.category == "other":
            continue  # never store unrelated mail
        if db.scalar(
            select(Communication.id).where(
                Communication.user_id == user.id, Communication.channel == "email", Communication.external_id == m["id"]
            )
        ):
            continue
        app = _match_application(db, user.id, m["subject"], m["from"], m["snippet"])
        db.add(
            Communication(
                user_id=user.id,
                application_id=app.id if app else None,
                channel="email",
                external_id=m["id"],
                direction="inbound",
                sender=m["from"][:300],
                recipient=m["to"][:300],
                subject=m["subject"][:500],
                snippet=m["snippet"],
                category=c.category,
                classified_by="rules",
                occurred_at=datetime.fromtimestamp(m["internal_date"] / 1000, tz=UTC)
                if m["internal_date"]
                else utcnow(),
            )
        )
        stored += 1
        if app and c.category == "application_confirmation" and app.submission_state in ("attempted", "unknown"):
            # A confirmation email from the employer is an external acknowledgement of the submission.
            app_svc.record_submission(db, user, app, "confirm", "system", reference=f"email:{m['id']}")
            acked += 1
        if app and c.category in ("interview_invitation", "offer", "rejection", "assessment"):
            audit.notify(
                db,
                user_id=user.id,
                kind=f"email_{c.category}",
                title=f"{c.category.replace('_', ' ').capitalize()}: {app.job.company.name}",
                body=m["subject"][:200],
                link=f"/applications/{app.id}",
            )
    if client.refreshed:
        integ.credentials_encrypted = seal(client.tokens)
    integ.status, integ.last_error, integ.last_synced_at = "connected", None, utcnow()
    db.commit()
    return {"scanned": len(messages), "stored": stored, "acknowledged": acked}


def draft_message(
    db: Session, user: User, kind: str, app: Application | None, recipient_name: str | None, context: str | None
) -> dict[str, Any]:
    if kind not in DRAFT_KINDS:
        raise ValueError("Unknown message type.")
    job_line = f"{app.job.title} at {app.job.company.name}" if app else "a role the candidate is interested in"
    try:
        res = ai.complete(
            db,
            user,
            feature=f"draft_{kind}",
            system="You write warm, professional, concise messages.",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["subject", "body"],
                "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
            },
            messages=[
                {
                    "role": "user",
                    "content": f"Write {DRAFT_KINDS[kind]} for {job_line}. Recipient: {recipient_name or 'unknown'}. "
                    "Use only facts from the profile; don't invent shared history, referrals or results. "
                    "No placeholders except [Recipient name] if the name is unknown. For LinkedIn types, "
                    "subject should be an empty string.\n\n"
                    f"Profile:\n{_profile_context(db, user)}\n\n"
                    f"Extra context from the user:\n{untrusted('user_notes', context or '', 2000)}",
                }
            ],
        )
        data = res.data or {}
        return {"subject": data.get("subject", ""), "body": data.get("body", ""), "generated_by": f"llm:{res.model}"}
    except AIUnavailable:
        return {
            "subject": "",
            "body": "",
            "generated_by": None,
            "error": "Connect an AI provider in Settings to generate drafts.",
        }
    except LLMError as exc:
        return {"subject": "", "body": "", "generated_by": None, "error": str(exc)}


def log_manual(
    db: Session,
    user: User,
    *,
    channel: str,
    direction: str,
    app: Application | None,
    subject: str | None,
    body: str,
    counterpart: str | None,
    category: str,
) -> Communication:
    c = Communication(
        user_id=user.id,
        application_id=app.id if app else None,
        channel=channel,
        external_id=f"manual:{secrets.token_hex(8)}",
        direction=direction,
        sender=counterpart if direction == "inbound" else None,
        recipient=counterpart if direction != "inbound" else None,
        subject=subject,
        body=body,
        snippet=body[:200],
        category=category,
        classified_by="user",
        occurred_at=utcnow(),
    )
    db.add(c)
    db.commit()
    return c
