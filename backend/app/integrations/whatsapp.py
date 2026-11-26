"""WhatsApp notifications through the official WhatsApp Business Cloud API (Meta Graph API).

Requires a Business account, an access token and a phone-number id (environment variables). Free-form
text messages are only delivered inside a 24-hour customer-service window; outside it Meta requires
pre-approved templates, so delivery failures are recorded rather than retried blindly.
"""

from __future__ import annotations

import httpx

from app.config import get_settings

GRAPH = "https://graph.facebook.com/v21.0"


class WhatsAppError(RuntimeError):
    pass


def send_text(to_number: str, body: str) -> str:
    s = get_settings()
    if not s.whatsapp_configured:
        raise WhatsAppError("WhatsApp requires JOBPILOT_WHATSAPP_ACCESS_TOKEN and JOBPILOT_WHATSAPP_PHONE_NUMBER_ID.")
    r = httpx.post(
        f"{GRAPH}/{s.whatsapp_phone_number_id}/messages",
        timeout=15,
        headers={"Authorization": f"Bearer {s.whatsapp_access_token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to_number.lstrip("+"),
            "type": "text",
            "text": {"body": body[:1000], "preview_url": False},
        },
    )
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        raise WhatsAppError(f"WhatsApp API error {r.status_code}: {detail}"[:300])
    messages = r.json().get("messages") or [{}]
    return messages[0].get("id", "")
