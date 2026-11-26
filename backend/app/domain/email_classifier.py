"""Rule-based classification of job-search email. Conservative: anything unclear is `other` and is
not stored, so unrelated mail is never treated as job communication."""

from __future__ import annotations

import re
from dataclasses import dataclass

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "offer",
        re.compile(r"\b(offer letter|pleased to offer|extend (you )?an offer|job offer|offer of employment)\b", re.I),
    ),
    (
        "rejection",
        re.compile(
            r"\b(unfortunately|not (be )?moving forward|decided to (move|proceed) with other|"
            r"other candidates|not been selected|regret to inform|position has been filled)\b",
            re.I,
        ),
    ),
    (
        "interview_invitation",
        re.compile(
            r"\b(interview (invitation|invite|request|scheduled)|schedule (an|your|a) "
            r"(interview|call|chat)|invite you to (an )?interview|calendly\.com|"
            r"availability for (an? )?(interview|call)|phone screen|technical interview)\b",
            re.I,
        ),
    ),
    (
        "assessment",
        re.compile(
            r"\b(online assessment|coding (challenge|assessment|test)|take[- ]home|hackerrank|"
            r"codility|codesignal|testgorilla|hackerearth|assignment)\b",
            re.I,
        ),
    ),
    (
        "application_confirmation",
        re.compile(
            r"\b(thank(s| you) for (applying|your application|your interest)|"
            r"application (has been )?(received|submitted)|we('ve| have) received your "
            r"application|successfully applied)\b",
            re.I,
        ),
    ),
    (
        "recruiter_outreach",
        re.compile(
            r"\b(recruiter|talent acquisition|came across your profile|opportunity at|"
            r"exciting opportunity|open role|hiring for)\b",
            re.I,
        ),
    ),
)


@dataclass(frozen=True)
class Classification:
    category: str
    matched: str | None


def classify(subject: str, snippet: str, sender: str = "") -> Classification:
    blob = f"{subject}\n{snippet}"
    for category, pattern in RULES:
        m = pattern.search(blob)
        if m:
            return Classification(category, m.group(0))
    if re.search(r"(greenhouse|lever|ashbyhq|workday|smartrecruiters|icims|jobvite)\.", sender, re.I):
        return Classification("ats_update", None)
    return Classification("other", None)


def sender_domain(sender: str) -> str | None:
    m = re.search(r"@([A-Za-z0-9.-]+)", sender)
    return m.group(1).lower() if m else None
