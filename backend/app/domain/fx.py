"""Currency identification and conversion to INR for salary eligibility.

Exchange rates are never guessed or hard-coded. The operator supplies a rate snapshot as a JSON file
(`JOBPILOT_FX_RATES_FILE`) that names its source and date:

    {"source": "Reserve Bank of India reference rates", "as_of": "2026-11-27",
     "rates_to_inr": {"USD": 84.20, "PLN": 21.05, "EUR": 89.10}}

A rate is used only while the snapshot is at most `JOBPILOT_FX_MAX_AGE_DAYS` old. With no snapshot, a
missing currency, or a stale snapshot, foreign pay goes to manual review instead of being converted.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

# Symbols and words that identify exactly one currency. A bare "$" is deliberately absent: it is used
# by USD, CAD, AUD, SGD and others, so it is ambiguous and leads to manual review.
_ALIASES = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "rupees": "INR",
    "us$": "USD",
    "usd": "USD",
    "c$": "CAD",
    "ca$": "CAD",
    "a$": "AUD",
    "au$": "AUD",
    "s$": "SGD",
    "€": "EUR",
    "£": "GBP",
    "zł": "PLN",
    "zl": "PLN",
    "pln": "PLN",
}
_ISO = re.compile(r"^[A-Za-z]{3}$")
AMBIGUOUS = frozenset({"$"})


def identify(raw: str | None) -> str | None:
    """ISO 4217 code for a currency marker, or None when it is missing or ambiguous (e.g. a bare "$")."""
    if not raw:
        return None
    token = raw.strip()
    if not token or token in AMBIGUOUS:
        return None
    alias = _ALIASES.get(token.lower())
    if alias:
        return alias
    return token.upper() if _ISO.match(token) else None


@dataclass(frozen=True)
class RateTable:
    source: str
    as_of: date
    rates_to_inr: dict[str, float]

    def meta(self) -> dict[str, str]:
        return {"source": self.source, "as_of": self.as_of.isoformat()}


@dataclass(frozen=True)
class FxContext:
    table: RateTable | None
    today: date
    max_age_days: int


@dataclass(frozen=True)
class Conversion:
    ok: bool
    amount_inr: float | None
    code: str  # FX_NOT_NEEDED | FX_CONVERTED | FX_NO_TABLE | FX_NO_RATE | FX_STALE
    note: str
    rate: float | None = None


def to_inr(amount: float, currency: str, fx: FxContext | None) -> Conversion:
    if currency == "INR":
        return Conversion(True, amount, "FX_NOT_NEEDED", "Listed in INR.")
    table = fx.table if fx else None
    if table is None:
        return Conversion(False, None, "FX_NO_TABLE", f"No exchange-rate snapshot is configured to convert {currency}.")
    age = (fx.today - table.as_of).days if fx else 0
    if fx and age > fx.max_age_days:
        return Conversion(
            False,
            None,
            "FX_STALE",
            f"The exchange rates ({table.source}, {table.as_of.isoformat()}) are {age} days old; the limit is "
            f"{fx.max_age_days} days.",
        )
    rate = table.rates_to_inr.get(currency)
    if rate is None:
        return Conversion(False, None, "FX_NO_RATE", f"No {currency}→INR rate in the snapshot ({table.source}).")
    return Conversion(
        True,
        amount * rate,
        "FX_CONVERTED",
        f"Converted at {rate:g} INR/{currency} ({table.source}, {table.as_of.isoformat()}).",
        rate,
    )


def parse_rate_table(data: object) -> RateTable:
    """Validate a rate snapshot. Raises ValueError with a readable message."""
    if not isinstance(data, dict):
        raise ValueError("rate snapshot must be a JSON object")
    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("'source' (where the rates came from) is required")
    try:
        as_of = date.fromisoformat(str(data.get("as_of")))
    except ValueError as exc:
        raise ValueError("'as_of' must be a YYYY-MM-DD date") from exc
    rates = data.get("rates_to_inr")
    if not isinstance(rates, dict) or not rates:
        raise ValueError("'rates_to_inr' must map currency codes to INR per unit")
    clean: dict[str, float] = {}
    for code, value in rates.items():
        if not (isinstance(code, str) and _ISO.match(code)):
            raise ValueError(f"invalid currency code {code!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"rate for {code} must be a positive number")
        clean[code.upper()] = float(value)
    return RateTable(source.strip(), as_of, clean)


_cache: dict[tuple[str, float], RateTable | None] = {}


def load_rate_table(path: str | None) -> RateTable | None:
    """The configured snapshot, re-read when the file changes. Invalid files are ignored (logged), so
    foreign pay falls back to manual review rather than a wrong conversion."""
    if not path:
        return None
    p = Path(path)
    try:
        key = (str(p), os.path.getmtime(p))
    except OSError:
        log.warning("fx_rates_unavailable", extra={"path": str(p)})
        return None
    if key not in _cache:
        try:
            _cache.clear()
            _cache[key] = parse_rate_table(json.loads(p.read_text()))
        except (OSError, ValueError) as exc:
            log.warning("fx_rates_invalid", extra={"path": str(p), "error": str(exc)})
            _cache[key] = None
    return _cache[key]


def current_context(today: date | None = None) -> FxContext:
    from app.config import get_settings

    s = get_settings()
    return FxContext(load_rate_table(s.fx_rates_file), today or date.today(), s.fx_max_age_days)
