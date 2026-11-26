from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select, update

from app.api.deps import DB, CurrentPrincipal, Principal, web_only
from app.db import utcnow
from app.models import AIUsage, AuditEvent, Notification
from app.services import insights as svc

router = APIRouter(tags=["insights"])


@router.get("/dashboard")
def dashboard(db: DB, p: CurrentPrincipal) -> dict:
    return svc.dashboard(db, p.user)


@router.get("/analytics")
def analytics(db: DB, p: CurrentPrincipal, start: datetime | None = None, end: datetime | None = None) -> dict:
    end = end or utcnow()
    start = start or end - timedelta(days=30)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(400, "Dates must include a timezone.")
    if start >= end or (end - start).days > 400:
        raise HTTPException(400, "Choose a range of up to 400 days.")
    return svc.analytics(db, p.user, start, end)


class NotificationOut(BaseModel):
    id: str
    kind: str
    title: str
    body: str | None
    link: str | None
    read_at: datetime | None
    created_at: datetime


@router.get("/notifications")
def notifications(db: DB, p: CurrentPrincipal, unread_only: bool = False) -> dict:
    q = select(Notification).where(Notification.user_id == p.id).order_by(Notification.created_at.desc()).limit(100)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    unread = (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == p.id, Notification.read_at.is_(None))
        )
        or 0
    )
    return {"unread": unread, "items": [NotificationOut.model_validate(n, from_attributes=True) for n in db.scalars(q)]}


class MarkRead(BaseModel):
    ids: list[str] | None = None  # None = all


@router.post("/notifications/read", status_code=204)
def mark_read(body: MarkRead, db: DB, p: Principal = Depends(web_only)) -> None:
    q = update(Notification).where(Notification.user_id == p.id, Notification.read_at.is_(None))
    if body.ids:
        q = q.where(Notification.id.in_(body.ids))
    db.execute(q.values(read_at=utcnow()))
    db.commit()


@router.get("/audit")
def audit_log(db: DB, p: CurrentPrincipal, limit: int = 100) -> list[dict]:
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.user_id == p.id)
        .order_by(AuditEvent.created_at.desc())
        .limit(min(limit, 500))
    )
    return [
        {
            "id": e.id,
            "actor": e.actor,
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "metadata": e.meta,
            "created_at": e.created_at,
        }
        for e in rows
    ]


@router.get("/ai/usage")
def ai_usage(db: DB, p: CurrentPrincipal, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=min(days, 365))
    rows = db.execute(
        select(
            AIUsage.feature,
            AIUsage.model,
            func.count(),
            func.sum(AIUsage.input_tokens),
            func.sum(AIUsage.output_tokens),
            func.sum(AIUsage.cost_usd),
            func.sum(case((AIUsage.success.is_(True), 1), else_=0)),
        )
        .where(AIUsage.user_id == p.id, AIUsage.created_at >= since)
        .group_by(AIUsage.feature, AIUsage.model)
    ).all()
    from app.ai.service import spend_today
    from app.config import get_settings

    return {
        "since": since,
        "spend_24h_usd": round(spend_today(db, p.id), 4),
        "daily_budget_usd": get_settings().llm_daily_budget_usd,
        "by_feature": [
            {
                "feature": f,
                "model": m,
                "calls": c,
                "input_tokens": i or 0,
                "output_tokens": o or 0,
                "cost_usd": round(cost, 4) if cost is not None else None,
                "successful": s or 0,
            }
            for f, m, c, i, o, cost, s in rows
        ],
    }
