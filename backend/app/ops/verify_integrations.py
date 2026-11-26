"""Verify JobPilot's external integrations against the real providers.

    python -m app.ops.verify_integrations                    # configuration only, no network calls
    python -m app.ops.verify_integrations --live             # + real, read-only/low-cost provider calls
    python -m app.ops.verify_integrations --live --gmail-user you@example.com
    python -m app.ops.verify_integrations --live --whatsapp-send-to +9198…   # sends ONE test message
    python -m app.ops.verify_integrations --live --gmail-user … --gmail-draft-to you@example.com

Every check reports PASS, FAIL or BLOCKED (with the exact configuration that is missing). Live AI
feature checks run against a throwaway SQLite database and file store — never the configured
database — and print token usage and cost. Secrets are never printed. Exit status is 1 if any check FAILs.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    setup: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


AI_SETUP = [
    "Set JOBPILOT_LLM_PROVIDER=anthropic (or openai for any OpenAI-compatible endpoint)",
    "Set JOBPILOT_LLM_MODEL (e.g. claude-opus-5-5) and JOBPILOT_LLM_API_KEY in the server environment / .env",
    "Optional: JOBPILOT_LLM_BASE_URL for OpenAI-compatible gateways; JOBPILOT_LLM_DAILY_BUDGET_USD",
    "Verify: python -m app.ops.verify_integrations --live",
]
GMAIL_SETUP = [
    "Google Cloud Console → create a project → enable the Gmail API",
    "OAuth consent screen: External (or Internal for Workspace); add scopes gmail.readonly and gmail.compose; "
    "add your account as a test user while the app is in Testing",
    "Credentials → OAuth client ID → Web application; Authorized redirect URI: "
    "<JOBPILOT_PUBLIC_APP_URL>/api/v1/integrations/gmail/callback",
    "Set JOBPILOT_GOOGLE_CLIENT_ID and JOBPILOT_GOOGLE_CLIENT_SECRET; restart the API",
    "In JobPilot: Integrations → Connect Gmail (completes OAuth and stores encrypted tokens)",
    "Verify: python -m app.ops.verify_integrations --live --gmail-user <your JobPilot login email>",
]
WHATSAPP_SETUP = [
    "Meta for Developers → create an app → add the WhatsApp product (WhatsApp Business Cloud API)",
    "Use the test phone number Meta provides (or a registered business number); note its Phone number ID",
    "Create a System User access token with whatsapp_business_messaging permission",
    "Add your personal number as a test recipient and send any message to the business number first "
    "(free-form messages are only delivered inside the 24-hour customer-service window)",
    "Set JOBPILOT_WHATSAPP_ACCESS_TOKEN and JOBPILOT_WHATSAPP_PHONE_NUMBER_ID",
    "Verify: python -m app.ops.verify_integrations --live --whatsapp-send-to +<your number>",
]


def _run(name: str, fn: Callable[[], Check]) -> Check:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - every failure becomes a FAIL row, never a crash
        tb = traceback.format_exception_only(type(exc), exc)[-1].strip()
        return Check(name, FAIL, tb[:300])


# ── Infrastructure ─────────────────────────────────────────────────────────────────────────────
def check_database() -> Check:
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from app.db import get_engine

    backend = Path(__file__).resolve().parents[2]
    head = ScriptDirectory.from_config(Config(str(backend / "alembic.ini"))).get_current_head()
    with get_engine().connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    dialect = get_engine().dialect.name
    if current != head:
        return Check("database", FAIL, f"{dialect}: schema at {current}, head is {head}. Run `alembic upgrade head`.")
    return Check("database", PASS, f"{dialect}: schema at head ({head})")


def check_redis() -> Check:
    s = get_settings()
    if not s.redis_url:
        status = FAIL if s.env == "production" else BLOCKED
        return Check(
            "redis",
            status,
            "JOBPILOT_REDIS_URL not set — rate limits are per-process",
            ["Set JOBPILOT_REDIS_URL=redis://<host>:6379/0 (required in production)"],
        )
    from app.security.rate_limit import RedisRateLimiter

    rl = RedisRateLimiter(s.redis_url, prefix="jp:verify:")
    if not rl.ping():
        return Check("redis", FAIL, "Redis did not answer PING")
    ok = [rl.allow("probe", 2, 5) for _ in range(3)] == [True, True, False]
    rl.reset()
    return Check("redis", PASS if ok else FAIL, "PING ok; sliding window enforced" if ok else "window not enforced")


# ── AI ─────────────────────────────────────────────────────────────────────────────────────────
def check_ai_config() -> Check:
    s = get_settings()
    if not s.ai_configured:
        missing = [
            n
            for n, v in (
                ("JOBPILOT_LLM_PROVIDER", s.llm_provider != "none"),
                ("JOBPILOT_LLM_MODEL", s.llm_model),
                ("JOBPILOT_LLM_API_KEY", s.llm_api_key),
            )
            if not v
        ]
        return Check("ai.config", BLOCKED, "Not configured; missing: " + ", ".join(missing), AI_SETUP)
    from app.ai.provider import PRICES

    priced = "known price" if s.llm_model in PRICES else "no price entry → budget uses conservative estimate"
    return Check("ai.config", PASS, f"{s.llm_provider} · {s.llm_model} ({priced}); key present, not shown")


def _ai_live_checks() -> list[Check]:
    from app.ai.provider import ToolSpec, build_provider

    llm = build_provider()
    out: list[Check] = []

    def timed(name: str, fn: Callable[[], tuple[bool, str, Any]]) -> None:
        t = time.monotonic()
        try:
            ok, detail, res = fn()
            m = {"latency_ms": int((time.monotonic() - t) * 1000)}
            if res is not None:
                m.update(
                    input_tokens=res.input_tokens, output_tokens=res.output_tokens, cost_usd=res.budget_cost_usd[0]
                )
            out.append(Check(name, PASS if ok else FAIL, detail, metrics=m))
        except Exception as exc:  # noqa: BLE001
            out.append(Check(name, FAIL, f"{type(exc).__name__}: {exc}"[:300]))

    def completion():
        r = llm.complete(
            system="Reply with exactly: OK", messages=[{"role": "user", "content": "Health check"}], max_tokens=64
        )
        return "OK" in r.text.upper(), f"model={r.model} replied {r.text.strip()[:40]!r}", r

    def structured():
        schema = {
            "type": "object",
            "properties": {"sum": {"type": "integer"}},
            "required": ["sum"],
            "additionalProperties": False,
        }
        r = llm.complete(
            system="Answer as JSON.",
            messages=[{"role": "user", "content": "What is 2+3?"}],
            json_schema=schema,
            max_tokens=256,
        )
        return r.data == {"sum": 5}, f"parsed {r.data}", r

    def tools():
        spec = ToolSpec(
            "get_time_zone",
            "Return the user's time zone",
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        r = llm.complete(
            system="Use the tool to answer.",
            messages=[{"role": "user", "content": "What is my time zone?"}],
            tools=[spec],
            max_tokens=512,
        )
        return (
            bool(r.tool_calls) and r.tool_calls[0]["name"] == "get_time_zone",
            f"tool_calls={[c['name'] for c in r.tool_calls]}",
            r,
        )

    timed("ai.completion", completion)
    timed("ai.structured_output", structured)
    timed("ai.tool_use", tools)
    return out


def _sample_docx() -> bytes:
    from docx import Document

    d = Document()
    d.add_paragraph("Verification Candidate")
    d.add_paragraph("verify@example.com | +91 90000 00000 | Bengaluru, Karnataka")
    d.add_heading("Summary", level=2)
    d.add_paragraph("Backend engineer with 3 years building Python and PostgreSQL services.")
    d.add_heading("Experience", level=2)
    p = d.add_paragraph()
    p.add_run("Software Engineer").bold = True
    p.add_run(" at Example Corp\tJan 2023 – Present")
    d.add_paragraph(
        "Responsible for REST APIs in Python and PostgreSQL used by 40 internal teams.", style="List Bullet"
    )
    d.add_paragraph("Worked on Docker-based CI pipelines.", style="List Bullet")
    d.add_heading("Skills", level=2)
    d.add_paragraph("Languages: SQL, Python, Docker, PostgreSQL")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _ai_feature_checks() -> list[Check]:
    """Exercise the real product features end to end on a throwaway database."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401
    from app import db as dbmod
    from app.integrations.sources.base import NormalizedJob
    from app.models import User, UserProfile, UserSettings
    from app.security.core import hash_password
    from app.security.uploads import FileStore, validate_resume_upload
    from app.services import applications as app_svc
    from app.services import assistant, interviews, resumes
    from app.services import jobs as jobs_svc

    out: list[Check] = []
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        dbmod.Base.metadata.create_all(engine)
        dbmod.set_engine(engine)
        store = FileStore(Path(tmp))
        db = dbmod.session_factory()()
        user = User(email="verify@example.com", password_hash=hash_password("not-used-0000"))
        user.profile = UserProfile(
            full_name="Verification Candidate", years_experience=3, target_titles=["Backend Engineer"]
        )
        user.settings = UserSettings()
        db.add(user)
        db.commit()
        jd = (
            "Requirements\n• 2-4 years building services in Python\n• PostgreSQL and Docker\nNice to have\n• Kubernetes"
        )
        jobs_svc.upsert_jobs(
            db,
            "verify:local",
            "Example Payments",
            [
                NormalizedJob(
                    provider="verify",
                    external_id="1",
                    company="Example Payments",
                    title="Backend Engineer",
                    url="https://example.invalid/job/1",
                    apply_url=None,
                    description_html="",
                    description_text=jd,
                ).enrich()
            ],
        )
        db.commit()
        from app.models import Job

        job = db.query(Job).one()
        resume = resumes.create_resume(db, store, user, validate_resume_upload("cv.docx", _sample_docx()), "Verify")

        def feature(name: str, fn: Callable[[], tuple[bool, str]]) -> None:
            try:
                ok, detail = fn()
                out.append(Check(name, PASS if ok else FAIL, detail))
            except Exception as exc:  # noqa: BLE001
                out.append(Check(name, FAIL, f"{type(exc).__name__}: {exc}"[:300]))

        def rewording():
            v = resumes.tailor(db, store, user, resume, job, actor="verify")
            rewrites = [c for c in v.changes if c["kind"] == "rewrite"]
            flagged = sum(1 for c in rewrites if c["flags"])
            ok = v.generated_by.startswith("llm:") and not (v.analysis.get("ai_note") or "").startswith(
                "AI rewriting was skipped"
            )
            return ok, f"{v.generated_by}: {len(rewrites)} rewrite(s), {flagged} flagged by the fabrication guard"

        def cover_letter():
            a = app_svc.create(db, user, job, actor="verify")
            res = app_svc.prepare(
                db,
                user,
                a,
                resume_id=None,
                resume_version_id=None,
                questions=["Why do you want this role?", "What is your passport number?"],
                generate_cover_letter=True,
                actor="verify",
            )
            db.refresh(a)
            unknown = next(x for x in a.answers if x.question.startswith("What is your passport"))
            ok = (
                bool(a.cover_letter)
                and (a.cover_letter_generated_by or "").startswith("llm:")
                and unknown.answer is None
            )
            return ok, (
                f"cover letter {len(a.cover_letter or '')} chars; unknown answer left blank={unknown.answer is None}; "
                f"notes={res['notes']}"
            )

        def interview():
            s = interviews.create_session(db, user, job, None)
            q = next(x for x in s.questions if x.get("id") != "_meta")
            graded = interviews.evaluate_answer(
                db,
                user,
                s,
                q["id"],
                "At Example Corp I had to speed up an API, so I "
                "added indexes in PostgreSQL and as a result p95 latency dropped 30%.",
            )
            fb = graded["feedback"]
            ok = (
                s.generated_by.startswith("llm:") and fb.get("method", "").startswith("llm:") and bool(fb.get("scores"))
            )
            return (
                ok,
                f"plan by {s.generated_by} ({len(s.questions) - 1} questions); feedback scores={fb.get('scores')}",
            )

        def assistant_chat():
            from app.models import AIConversation

            c = AIConversation(user_id=user.id, title="verify")
            db.add(c)
            db.commit()
            msgs = assistant.send(db, user, c, "Which job in my list fits me best? Use your tools.")
            tools_used = [t["name"] for m in msgs if m.role == "assistant" for t in (m.tool_calls or [])]
            final = [m for m in msgs if m.role == "assistant" and m.content.strip()]
            return bool(final) and bool(
                tools_used
            ), f"tools used={tools_used}; reply {len(final[-1].content) if final else 0} chars"

        feature("ai.feature.resume_rewording", rewording)
        feature("ai.feature.cover_letter", cover_letter)
        feature("ai.feature.interview_feedback", interview)
        feature("ai.feature.assistant", assistant_chat)
        from sqlalchemy import func, select

        from app.models import AIUsage

        spent = db.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)))
        for c in out:
            c.metrics.setdefault("total_feature_cost_usd", round(float(spent or 0), 4))
        db.close()
        engine.dispose()
    return out


# ── Gmail ──────────────────────────────────────────────────────────────────────────────────────
def check_gmail_config() -> Check:
    s = get_settings()
    if not s.gmail_configured:
        missing = [
            n
            for n, v in (
                ("JOBPILOT_GOOGLE_CLIENT_ID", s.google_client_id),
                ("JOBPILOT_GOOGLE_CLIENT_SECRET", s.google_client_secret),
            )
            if not v
        ]
        return Check("gmail.config", BLOCKED, "Not configured; missing: " + ", ".join(missing), GMAIL_SETUP)
    from app.integrations.gmail import redirect_uri

    return Check(
        "gmail.config", PASS, f"client configured; redirect URI must be registered exactly as {redirect_uri()}"
    )


def check_gmail_live(email: str | None, draft_to: str | None) -> list[Check]:
    if not get_settings().gmail_configured:
        return [Check("gmail.live", BLOCKED, "Gmail OAuth client not configured", GMAIL_SETUP)]
    if not email:
        return [
            Check(
                "gmail.live",
                BLOCKED,
                "No connected account selected; pass --gmail-user <JobPilot login email> "
                "after connecting Gmail in the app",
                GMAIL_SETUP[-2:],
            )
        ]
    from sqlalchemy import select

    from app.db import session_factory
    from app.domain.email_classifier import classify
    from app.integrations.gmail import GmailClient, seal, unseal
    from app.models import Integration, User

    with session_factory()() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        integ = user and db.scalar(
            select(Integration).where(Integration.user_id == user.id, Integration.provider == "gmail")
        )
        if not integ or integ.status != "connected" or not integ.credentials_encrypted:
            return [Check("gmail.live", BLOCKED, f"{email} has not connected Gmail in JobPilot", GMAIL_SETUP[-2:])]
        client = GmailClient(unseal(integ.credentials_encrypted))
        out = [
            Check(
                "gmail.oauth",
                PASS,
                f"token valid for {client.profile_email()}" + (" (refreshed)" if client.refreshed else ""),
            )
        ]
        msgs = client.recent_job_messages(limit=25)
        cats: dict[str, int] = {}
        for m in msgs:
            c = classify(m["subject"], m["snippet"], m["from"]).category
            cats[c] = cats.get(c, 0) + 1
        out.append(
            Check(
                "gmail.read_and_classify",
                PASS,
                f"read {len(msgs)} message headers (read-only, nothing stored); categories={cats}",
            )
        )
        if draft_to:
            did = client.create_draft(
                draft_to,
                "JobPilot verification draft",
                "This draft was created by JobPilot's integration check. You can delete it.",
            )
            out.append(Check("gmail.draft", PASS, f"draft {did[:6]}… created in the connected mailbox (not sent)"))
        else:
            out.append(
                Check("gmail.draft", BLOCKED, "Skipped: creates a real draft; pass --gmail-draft-to <address> to run")
            )
        if client.refreshed:
            integ.credentials_encrypted = seal(client.tokens)
            db.commit()
        return out


# ── WhatsApp ───────────────────────────────────────────────────────────────────────────────────
def check_whatsapp_config() -> Check:
    s = get_settings()
    if not s.whatsapp_configured:
        missing = [
            n
            for n, v in (
                ("JOBPILOT_WHATSAPP_ACCESS_TOKEN", s.whatsapp_access_token),
                ("JOBPILOT_WHATSAPP_PHONE_NUMBER_ID", s.whatsapp_phone_number_id),
            )
            if not v
        ]
        return Check("whatsapp.config", BLOCKED, "Not configured; missing: " + ", ".join(missing), WHATSAPP_SETUP)
    return Check("whatsapp.config", PASS, "token and phone number id present (not shown)")


def check_whatsapp_live(send_to: str | None) -> list[Check]:
    s = get_settings()
    if not s.whatsapp_configured:
        return [Check("whatsapp.live", BLOCKED, "WhatsApp Cloud API not configured", WHATSAPP_SETUP)]
    import httpx

    from app.integrations.whatsapp import GRAPH, send_text

    r = httpx.get(
        f"{GRAPH}/{s.whatsapp_phone_number_id}",
        params={"fields": "display_phone_number,verified_name"},
        headers={"Authorization": f"Bearer {s.whatsapp_access_token}"},
        timeout=15,
    )
    if r.status_code != 200:
        msg = (
            (r.json().get("error", {}) or {}).get("message", "")
            if r.headers.get("content-type", "").startswith("application/json")
            else ""
        )
        return [Check("whatsapp.token", FAIL, f"Graph API rejected the token/phone id ({r.status_code}) {msg}"[:300])]
    info = r.json()
    out = [
        Check("whatsapp.token", PASS, f"phone number {info.get('display_phone_number')} ({info.get('verified_name')})")
    ]
    if send_to:
        mid = send_text(send_to, "JobPilot integration check: WhatsApp notifications are working.")
        out.append(
            Check("whatsapp.send", PASS, f"message accepted by Meta (id {mid[:12]}…); confirm it arrived on the phone")
        )
    else:
        out.append(
            Check("whatsapp.send", BLOCKED, "Skipped: sends a real message; pass --whatsapp-send-to +<number> to run")
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="make real provider calls (AI calls cost money)")
    ap.add_argument("--gmail-user")
    ap.add_argument("--gmail-draft-to")
    ap.add_argument("--whatsapp-send-to")
    ap.add_argument("--json", type=Path, help="also write the report as JSON")
    args = ap.parse_args()

    checks = [
        _run("database", check_database),
        _run("redis", check_redis),
        _run("ai.config", check_ai_config),
        _run("gmail.config", check_gmail_config),
        _run("whatsapp.config", check_whatsapp_config),
    ]
    ai_ok = checks[2].status == PASS
    if args.live:
        if ai_ok:
            checks += _ai_live_checks()
            checks += _ai_feature_checks()
        else:
            checks.append(Check("ai.live", BLOCKED, "AI provider not configured", AI_SETUP))
        try:
            checks += check_gmail_live(args.gmail_user, args.gmail_draft_to)
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("gmail.live", FAIL, f"{type(exc).__name__}: {exc}"[:300]))
        try:
            checks += check_whatsapp_live(args.whatsapp_send_to)
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("whatsapp.live", FAIL, f"{type(exc).__name__}: {exc}"[:300]))
    else:
        checks.append(Check("live", BLOCKED, "Live provider calls not requested (run with --live)"))

    width = max(len(c.name) for c in checks)
    for c in checks:
        metrics = f"  {c.metrics}" if c.metrics else ""
        print(f"{c.status:<8} {c.name:<{width}}  {c.detail}{metrics}")
        for step in c.setup:
            print(f"{'':<8} {'':<{width}}  → {step}")
    counts = {k: sum(c.status == k for c in checks) for k in (PASS, FAIL, BLOCKED)}
    print(f"\n{counts[PASS]} passed · {counts[FAIL]} failed · {counts[BLOCKED]} blocked")
    if args.json:
        args.json.write_text(json.dumps([asdict(c) for c in checks], indent=2))
    sys.exit(1 if counts[FAIL] else 0)


if __name__ == "__main__":
    main()
