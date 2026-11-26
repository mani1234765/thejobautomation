"""AI gateway: every model call goes through here for budgets, usage accounting and error mapping."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.guards import SYSTEM_RULES
from app.ai.provider import AIUnavailable, LLMError, LLMProvider, LLMResult, ToolSpec, build_provider
from app.config import get_settings
from app.db import session_factory, utcnow
from app.models import AIUsage, User


class BudgetExceeded(LLMError):
    pass


def status() -> dict[str, Any]:
    s = get_settings()
    return {
        "configured": s.ai_configured,
        "provider": s.llm_provider if s.ai_configured else None,
        "model": s.llm_model if s.ai_configured else None,
        "allowed_models": allowed_models() if s.ai_configured else [],
    }


def spend_today(db: Session, user_id: str) -> float:
    since = utcnow() - timedelta(hours=24)
    total = db.scalar(
        select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(
            AIUsage.user_id == user_id, AIUsage.created_at >= since
        )
    )
    return float(total or 0.0)


def allowed_models() -> list[str]:
    s = get_settings()
    extra = [m.strip() for m in (s.llm_allowed_models or "").split(",") if m.strip()]
    return list(dict.fromkeys([s.llm_model, *extra])) if s.llm_model else extra


def provider_for(user: User) -> LLMProvider:
    override = user.settings.ai_model_override if user.settings else None
    # Overrides are honoured only for operator-approved models (cost and data-handling control).
    return build_provider(override if override and override in allowed_models() else None)


def complete(
    db: Session,
    user: User,
    *,
    feature: str,
    system: str,
    messages: list[dict[str, Any]],
    json_schema: dict | None = None,
    tools: list[ToolSpec] | None = None,
    max_tokens: int | None = None,
    provider: LLMProvider | None = None,
) -> LLMResult:
    """Run one model call with budget enforcement and usage logging."""
    budget = get_settings().llm_daily_budget_usd
    if spend_today(db, user.id) >= budget:
        raise BudgetExceeded(f"Daily AI budget of ${budget:.2f} reached. It resets on a rolling 24h window.")
    llm = provider or provider_for(user)
    usage = AIUsage(user_id=user.id, provider=llm.name, model=llm.model, feature=feature, created_at=utcnow())
    try:
        result = llm.complete(
            system=f"{SYSTEM_RULES}\n\n{system}",
            messages=messages,
            json_schema=json_schema,
            tools=tools,
            max_tokens=max_tokens,
        )
    except LLMError as exc:
        usage.success, usage.error = False, str(exc)[:300]
        _log_usage(usage)
        raise
    usage.model = result.model
    usage.input_tokens, usage.output_tokens = result.input_tokens, result.output_tokens
    (usage.cost_usd, usage.cost_estimated), usage.latency_ms = result.budget_cost_usd, result.latency_ms
    _log_usage(usage)
    return result


def _log_usage(usage: AIUsage) -> None:
    """Usage rows are written in their own session so they never commit the caller's transaction."""
    with session_factory()() as s:
        s.add(usage)
        s.commit()


__all__ = ["AIUnavailable", "BudgetExceeded", "LLMError", "complete", "status", "spend_today"]
