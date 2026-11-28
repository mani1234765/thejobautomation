"""Effective policy settings: stored values made safe to use and to show.

Values are normally validated on the way in (`SettingsIn`), but a row can still hold something out of
range: an older version allowed different bounds, or the database was edited by hand. Rather than fail
(GET /settings used to return 500), each value is normalised to the nearest valid one and every change is
reported. Nothing is written back here: the stored value stays as it is until the user saves Settings.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.salary_policy import (
    APPRENTICE_CURRENCIES,
    APPRENTICE_DEFAULTS,
    DEFAULT_MIN_CTC_LPA,
    MAX_APPRENTICE_STIPEND,
    MAX_DAILY_APPLICATIONS,
    MAX_MIN_CTC_LPA,
)
from app.models import UserSettings

log = logging.getLogger(__name__)
_PHONE = re.compile(r"^\+?[1-9]\d{7,14}$")


@dataclass
class Effective:
    values: dict[str, Any]
    normalized: list[dict[str, Any]] = field(default_factory=list)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def effective(s: UserSettings) -> Effective:
    out = Effective(
        {
            "min_ctc_lpa": s.min_ctc_lpa,
            "allow_undisclosed_salary": s.allow_undisclosed_salary,
            "min_stipend_monthly_inr": s.min_stipend_monthly_inr,
            "allow_undisclosed_stipend": s.allow_undisclosed_stipend,
            "daily_application_limit": s.daily_application_limit,
            "require_approval": s.require_approval,
            "min_match_score": s.min_match_score,
            "ai_model_override": s.ai_model_override,
            "notify_in_app": s.notify_in_app,
            "notify_email": s.notify_email,
            "notify_whatsapp": s.notify_whatsapp,
            "whatsapp_number": s.whatsapp_number,
            "daily_summary": s.daily_summary,
            **{k: getattr(s, k) for k in APPRENTICE_DEFAULTS},
        }
    )
    v = out.values

    def fix(name: str, value: Any, reason: str) -> None:
        out.normalized.append({"field": name, "stored": v[name], "effective": value, "reason": reason})
        v[name] = value

    limit = _num(v["daily_application_limit"])
    if limit is None or limit != int(limit):
        fix("daily_application_limit", MAX_DAILY_APPLICATIONS, "not a whole number; using the default")
    elif limit < 1:
        fix("daily_application_limit", 1, "below the minimum of 1")
    elif limit > MAX_DAILY_APPLICATIONS:
        fix("daily_application_limit", MAX_DAILY_APPLICATIONS, f"above the maximum of {MAX_DAILY_APPLICATIONS:,}")

    ctc = _num(v["min_ctc_lpa"])
    if ctc is None:
        fix("min_ctc_lpa", DEFAULT_MIN_CTC_LPA, "not a valid number; using the default")
    elif ctc < 0:
        fix("min_ctc_lpa", 0.0, "below 0")
    elif ctc > MAX_MIN_CTC_LPA:
        fix("min_ctc_lpa", MAX_MIN_CTC_LPA, f"above the maximum of {MAX_MIN_CTC_LPA:g} LPA")
    elif round(ctc, 2) != ctc:
        fix("min_ctc_lpa", round(ctc, 2), "more than two decimals")

    stipend = v["min_stipend_monthly_inr"]
    if stipend is not None:
        st = _num(stipend)
        if st is None:
            fix("min_stipend_monthly_inr", None, "not a valid number; no minimum applied")
        elif st < 0:
            fix("min_stipend_monthly_inr", 0.0, "below 0")
        elif st > 1_000_000:
            fix("min_stipend_monthly_inr", 1_000_000.0, "above the maximum")

    appr = v["apprentice_min_stipend"]
    if appr is not None:
        a = _num(appr)
        if a is None:
            fix("apprentice_min_stipend", None, "not a valid number; no minimum applied")
        elif a < 0:
            fix("apprentice_min_stipend", 0.0, "below 0")
        elif a > MAX_APPRENTICE_STIPEND:
            fix("apprentice_min_stipend", MAX_APPRENTICE_STIPEND, "above the maximum")
    if v["apprentice_stipend_currency"] not in APPRENTICE_CURRENCIES:
        # Never replaced by a guessed currency: the minimum can't be compared until the user picks one.
        fix("apprentice_stipend_currency", None, "not a supported currency; choose one in Settings")
    for name, allowed in (
        ("apprentice_stipend_period", ("month", "year")),
        ("apprentice_advertised_stipend", ("compare", "review")),
        ("apprentice_unverified_outcome", ("pass", "review")),
    ):
        if v[name] not in allowed:
            fix(name, APPRENTICE_DEFAULTS[name], "not a valid choice; using the default")

    score = _num(v["min_match_score"])
    if score is None or score != int(score):
        fix("min_match_score", 55, "not a whole number; using the default")
    elif not 0 <= score <= 100:
        fix("min_match_score", int(min(100, max(0, score))), "outside 0–100")

    if v["require_approval"] is not True:
        fix("require_approval", True, "approval can't be disabled")
    if v["whatsapp_number"] and not _PHONE.match(str(v["whatsapp_number"])):
        fix("whatsapp_number", None, "not a valid international number")
    if v["ai_model_override"] and len(str(v["ai_model_override"])) > 120:
        fix("ai_model_override", None, "too long")

    if out.normalized:
        log.warning(
            "settings_normalized",
            extra={"user_id": s.user_id, "fields": [n["field"] for n in out.normalized]},
        )
    return out
