from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AuditEvent, Notification
from app.security.core import redact


def record(
    db: Session,
    *,
    user_id: str | None,
    actor: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    meta: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Append an audit event in the caller's transaction (committed with the action it describes)."""
    db.add(
        AuditEvent(
            user_id=user_id,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=redact(meta or {}),
            ip=ip,
            created_at=utcnow(),
        )
    )


def notify(
    db: Session, *, user_id: str, kind: str, title: str, body: str | None = None, link: str | None = None
) -> Notification:
    """Create an in-app notification. External channels are delivered by the worker (see worker/handlers)."""
    n = Notification(user_id=user_id, kind=kind, title=title, body=body, link=link, deliveries={})
    db.add(n)
    return n
