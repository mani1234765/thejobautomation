"""City-name normalisation for location matching.

Listings and users name the same city differently ("Bangalore" / "Bengaluru", "Bombay" / "Mumbai").
Each known alias maps to one canonical name; matching is on whole words, so "Pune" does not match
"Puneet Nagar". Unknown names fall back to their own lower-cased form.
"""

from __future__ import annotations

import re

# canonical → aliases (lower-case). Only renamings and widely used alternative spellings.
CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "bengaluru": ("bangalore", "bengaluru", "blr"),
    "mumbai": ("bombay", "mumbai"),
    "chennai": ("madras", "chennai"),
    "kolkata": ("calcutta", "kolkata"),
    "gurugram": ("gurgaon", "gurugram"),
    "delhi": ("delhi", "new delhi"),
    "pune": ("poona", "pune"),
    "thiruvananthapuram": ("trivandrum", "thiruvananthapuram"),
    "kochi": ("cochin", "kochi"),
    "mysuru": ("mysore", "mysuru"),
    "vadodara": ("baroda", "vadodara"),
    "puducherry": ("pondicherry", "puducherry"),
    "visakhapatnam": ("vizag", "visakhapatnam"),
    "mangaluru": ("mangalore", "mangaluru"),
    "hyderabad": ("hyderabad",),
}
_TO_CANON = {alias: canon for canon, aliases in CITY_ALIASES.items() for alias in aliases}


def canonical_city(name: str) -> str:
    """'Bangalore, India' → 'bengaluru'; unknown names → their first comma-separated part, lower-cased."""
    first = re.sub(r"\s+", " ", name.split(",")[0]).strip().lower()
    return _TO_CANON.get(first, first)


def cities_in(text: str | None) -> set[str]:
    """Canonical names of every known city mentioned in a location string (whole words only)."""
    low = (text or "").lower()
    return {canon for alias, canon in _TO_CANON.items() if re.search(rf"\b{re.escape(alias)}\b", low)}


def mentions_city(location: str | None, city: str) -> bool:
    """Does a listing's location mention the user's city (by any alias, as a whole word)?"""
    if not location:
        return False
    canon = canonical_city(city)
    if canon in cities_in(location):
        return True
    return bool(canon) and re.search(rf"\b{re.escape(canon)}\b", location.lower()) is not None
