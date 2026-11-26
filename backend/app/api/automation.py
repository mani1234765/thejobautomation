from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, require_scope
from app.db import utcnow
from app.models import AutomationConfig, AutomationExecution
from app.services import audit
from app.services import automation as svc
from app.worker import queue

router = APIRouter(prefix="/automations", tags=["automation"])


class ConfigIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["discovery", "preparation"]
    interval_minutes: int = Field(ge=30, le=7 * 24 * 60)
    filters: dict[str, Any] = Field(default_factory=dict)


class ConfigOut(BaseModel):
    id: str
    name: str
    kind: str
    interval_minutes: int
    filters: dict[str, Any]
    state: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime


class ExecutionOut(BaseModel):
    id: str
    config_id: str | None
    kind: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    stats: dict[str, Any]
    log: list[dict[str, Any]]
    error: str | None


_ALLOWED_FILTERS = {
    "discovery": {
        "min_score",
        "notify_min_score",
        "max_shortlist",
        "locations",
        "work_modes",
        "experience_levels",
        "employment_types",
        "title_keywords",
    },
    "preparation": {"min_score", "max_per_run"},
}


def _clean_filters(kind: str, filters: dict[str, Any]) -> dict[str, Any]:
    bad = set(filters) - _ALLOWED_FILTERS[kind]
    if bad:
        raise HTTPException(400, f"Unknown filters for {kind}: {', '.join(sorted(bad))}")
    out = {**svc.DEFAULT_FILTERS[kind], **filters}
    for k in ("min_score", "notify_min_score"):
        if k in out and not (0 <= int(out[k]) <= 100):
            raise HTTPException(400, f"{k} must be between 0 and 100.")
    for k in ("max_shortlist", "max_per_run"):
        if k in out and not (0 <= int(out[k]) <= 25):
            raise HTTPException(400, f"{k} must be between 0 and 25.")
    return out


@router.get("", response_model=list[ConfigOut])
def list_configs(db: DB, p: CurrentPrincipal) -> list[Any]:
    return list(
        db.scalars(
            select(AutomationConfig).where(AutomationConfig.user_id == p.id).order_by(AutomationConfig.created_at)
        )
    )


@router.post("", response_model=ConfigOut, status_code=201)
def create_config(
    body: ConfigIn, db: DB, p: Principal = Depends(require_scope("automation:write"))
) -> AutomationConfig:
    cfg = AutomationConfig(
        user_id=p.id,
        name=body.name,
        kind=body.kind,
        interval_minutes=body.interval_minutes,
        filters=_clean_filters(body.kind, body.filters),
        state="active",
    )
    svc.schedule_next(cfg)
    db.add(cfg)
    db.flush()
    audit.record(
        db,
        user_id=p.id,
        actor=p.actor,
        action="automation.created",
        entity_type="automation",
        entity_id=cfg.id,
        meta={"kind": body.kind},
    )
    db.commit()
    return cfg


@router.put("/{cid}", response_model=ConfigOut)
def update_config(
    cid: str, body: ConfigIn, db: DB, p: Principal = Depends(require_scope("automation:write"))
) -> AutomationConfig:
    cfg = get_owned(db, AutomationConfig, cid, p.id)
    cfg.name, cfg.interval_minutes = body.name, body.interval_minutes
    cfg.filters = _clean_filters(cfg.kind, body.filters)
    svc.schedule_next(cfg)
    db.commit()
    return cfg


@router.post("/{cid}/{action}", response_model=ConfigOut)
def control(
    cid: str,
    action: Literal["pause", "resume", "stop", "run"],
    db: DB,
    p: Principal = Depends(require_scope("automation:write")),
) -> AutomationConfig:
    cfg = get_owned(db, AutomationConfig, cid, p.id)
    if action == "run":
        if cfg.state == "stopped":
            raise HTTPException(400, "This automation is stopped. Resume it first.")
        queue.enqueue(
            db,
            "automation",
            {"config_id": cfg.id, "trigger": "manual"},
            user_id=p.id,
            idempotency_key=f"automation:{cfg.id}:manual:{utcnow().strftime('%Y%m%d%H%M')}",
            max_attempts=1,
        )
    else:
        cfg.state = {"pause": "paused", "resume": "active", "stop": "stopped"}[action]
        svc.schedule_next(cfg)
    audit.record(
        db, user_id=p.id, actor=p.actor, action=f"automation.{action}", entity_type="automation", entity_id=cfg.id
    )
    db.commit()
    return cfg


@router.delete("/{cid}", status_code=204)
def delete_config(cid: str, db: DB, p: Principal = Depends(require_scope("automation:write"))) -> None:
    db.delete(get_owned(db, AutomationConfig, cid, p.id))
    db.commit()


@router.get("/executions/recent", response_model=list[ExecutionOut])
def executions(db: DB, p: CurrentPrincipal, config_id: str | None = None, limit: int = 30) -> list[Any]:
    q = (
        select(AutomationExecution)
        .where(AutomationExecution.user_id == p.id)
        .order_by(AutomationExecution.started_at.desc())
        .limit(min(limit, 100))
    )
    if config_id:
        q = q.where(AutomationExecution.config_id == config_id)
    return list(db.scalars(q))


@router.get("/queue")
def queue_state(db: DB, p: CurrentPrincipal) -> dict:
    from app.models import Task

    rows = db.scalars(
        select(Task).where(Task.user_id == p.id, Task.status.in_(("queued", "running"))).order_by(Task.run_after)
    ).all()
    return {
        "pending": [
            {"id": t.id, "kind": t.kind, "status": t.status, "run_after": t.run_after, "attempts": t.attempts}
            for t in rows
        ]
    }
