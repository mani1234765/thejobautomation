# JobPilot V1.1 — Release Readiness Report

> Superseded by [RELEASE_READINESS_V1_2.md](RELEASE_READINESS_V1_2.md). Corrections since V1.1: a placeholder
> secret key was accepted in production (fixed); the proxy-trust guidance here (`--forwarded-allow-ips`) is
> replaced by the hop-count approach in DEPLOYMENT.md.

Date: 2026-11-26 · Scope: integration verification and production readiness (no new features) ·
**Deployment: not performed — awaiting explicit approval.**

## Verdict

**Not ready for a production release that advertises AI, Gmail or WhatsApp.** None of the three could be
verified against the real providers because no credentials were available. Everything else is ready to
deploy to a staging environment: the core product, security hardening, CI, CSP, shared rate limiting,
migrations and backups. Production can follow once the blocked integrations are verified (or deliberately
left disabled) and the owner approves.

## Completed (PASS)

| Item | Evidence |
|---|---|
| PRD audit | IMPLEMENTATION_PLAN.md §1–2: every FR classified PASS / PARTIAL / BLOCKED / NOT IMPLEMENTED |
| CI pipeline | `.github/workflows/ci.yml`: backend on PostgreSQL 17 + Redis 7, MCP, frontend, dependency audit, E2E on a production build, manual live-integration job. `actionlint` clean; all job commands reproduced locally |
| Content-Security-Policy | Strict per-request nonce CSP. E2E asserts nonce rotation and **zero violations** across the full journey on the production build. API sends `default-src 'none'` |
| Shared rate limiter | Redis sliding window (atomic Lua). Verified with two limiter instances sharing one window; fail-closed for auth, fail-open for API |
| Security review | 9 findings fixed with regression tests, including a **High** AI-budget bypass |
| Migrations | Up → check (no drift) → down → up, on SQLite and PostgreSQL 17. Two new reversible migrations |
| Backup & recovery | `app.ops.backup` (pg_dump/pg_restore or SQLite online backup + files + SHA-256 manifest). Drill: seed → backup → wipe → restore into a separate PostgreSQL DB → row counts and file bytes identical; tampered archive rejected |
| Integration verification tooling | `app.ops.verify_integrations`: config checks, live provider checks, live feature checks on a throwaway DB, side effects gated behind explicit flags |
| Offline integration contracts | Anthropic (official SDK), OpenAI-compatible, Google OAuth/Gmail, WhatsApp — request shapes, success and failure handling |
| Deployment plan | DEPLOYMENT.md: topology, secrets, proxy trust, release/rollback, backups, scaling limits, go/no-go |
| Readiness probe | `GET /api/v1/health/ready` checks the database and rate limiter (503 when degraded) |

## Failed (FAIL)

None. Two regressions were found and fixed during this milestone:

* **Escape key:** after the toast library swap, Escape dismissed a toast instead of closing the open dialog.
* **Toast list markup:** the toast list's ARIA markup broke list semantics (caught by axe).

Both now pass in E2E.

## Blocked — credentials not configured

| Integration | What works now | To unblock (exact configuration) | Verify with |
|---|---|---|---|
| **AI provider** (assistant, resume rewording, cover letters, application answers, interview plans and feedback) | Both adapters contract-tested; all four features tested with provider doubles; the app shows "Connect an AI provider" without a key | `JOBPILOT_LLM_PROVIDER=anthropic` (or `openai`), `JOBPILOT_LLM_MODEL=claude-opus-5-5` (or your model), `JOBPILOT_LLM_API_KEY=…`; optional `JOBPILOT_LLM_BASE_URL`, `JOBPILOT_LLM_ALLOWED_MODELS` | `python -m app.ops.verify_integrations --live` (≈ 7 model calls; costs cents) |
| **Gmail** (OAuth, reading, classification, drafts) | OAuth URL, code exchange, refresh, revocation handling, sync → classification → acknowledgement, draft creation — contract-tested | Google Cloud project with the Gmail API enabled; consent screen with `gmail.readonly` + `gmail.compose` and you as a test user; Web OAuth client with redirect URI `<JOBPILOT_PUBLIC_APP_URL>/api/v1/integrations/gmail/callback`; set `JOBPILOT_GOOGLE_CLIENT_ID` / `_SECRET`; then **Integrations → Connect Gmail** in the app | `python -m app.ops.verify_integrations --live --gmail-user <login email>` (+ `--gmail-draft-to <addr>` to create one real draft) |
| **WhatsApp** (notifications) | Request format, error mapping, worker delivery states (sent / disabled / not configured / failed, never re-sent) — tested | Meta app with WhatsApp Cloud API; test or business phone number id; system-user token with `whatsapp_business_messaging`; your number added as a test recipient (message the business number first — 24h window); set `JOBPILOT_WHATSAPP_ACCESS_TOKEN` / `_PHONE_NUMBER_ID` | `python -m app.ops.verify_integrations --live` (read-only token check) and `--whatsapp-send-to +<number>` (sends one message) |
| **CI on GitHub** | Workflow validated and every job reproduced locally | Create a GitHub repository, push, and add `integration-verification` environment secrets for the manual live job | Actions tab → CI |

Current verifier output on this machine: `1 passed · 0 failed · 7 blocked` (database PASS; Redis, AI, Gmail,
WhatsApp configuration and live checks BLOCKED).

## Security findings

| Severity | Finding | Status |
|---|---|---|
| High | AI daily budget bypass (unpriced models recorded no cost; arbitrary model overrides) | Fixed |
| Medium | Logout didn't revoke sessions server-side | Fixed |
| Medium | Uploads buffered fully before the size check | Fixed |
| Medium | Per-process rate limiting | Fixed (Redis) |
| Medium | No CSP | Fixed |
| Medium | MCP HTTP transport could be exposed unauthenticated | Fixed (loopback-only + DNS-rebinding protection) |
| Low | Account deletion without re-authentication | Fixed |
| Low | Malformed encryption key detected late | Fixed |
| Low | Misleading notification settings | Fixed |
| Medium | Per-IP limits spoofable if proxy trust is misconfigured | Open — configuration, documented |
| Medium | Local file storage blocks multi-instance API | Open — run one API instance until object storage exists |
| Low | No MFA, email verification or password reset | Open — backlog |
| Low | Audit retention not automated | Open |
| Low | API tokens don't expire (revocable) | Open |
| Info | Single signing secret; rotation signs everyone out | Accepted |

Also verified: approval enforcement unchanged and re-tested on PostgreSQL · cross-user isolation · CSRF · web-only
actions unreachable by tokens · no secrets in logs (scan of all run logs) · dependency audits: **0 known
vulnerabilities** (pip-audit ×2, npm audit).

## Test results (2026-11-26, this machine)

| Suite | Environment | Result |
|---|---|---|
| Backend | PostgreSQL 17 + Redis 7 | **99 passed**, coverage 85% |
| Backend | SQLite, no Redis | 97 passed, 2 skipped (Redis-only) |
| MCP server | — | 7 passed |
| Frontend unit/component | jsdom | 21 passed |
| E2E + axe + CSP | Production build, PostgreSQL + Redis, live public job board | 16 passed |
| Lint / format / types | ruff, eslint, tsc | Clean |
| Migrations | SQLite + PostgreSQL | Up/check/down/up clean |
| Dependency audit | pip-audit, npm audit | 0 known vulnerabilities |
| Live integrations | — | **BLOCKED** (no credentials) |

**Total: 143 automated tests passing, 0 failing.**

## Deployment readiness

| Area | Ready? |
|---|---|
| Build artifacts (API, worker, web) | ✅ Production build verified |
| Database migrations & rollback | ✅ |
| Backups & restore | ✅ Tooling and drill; schedule defined in DEPLOYMENT.md |
| Configuration guards | ✅ Production refuses unsafe config |
| Observability | 🟡 Structured logs, liveness and readiness probes; alerting and metrics to be set up on the chosen platform |
| Hosting, domain, TLS, secrets manager | ⛔ Owner decision (DEPLOYMENT.md §1) |
| Integrations | ⛔ Credentials |
| Deployment | **Not performed** — awaiting explicit approval |

## Remaining risks

1. **AI quality is unmeasured live.** Rewording faithfulness, answer quality and interview feedback have only
   been exercised with doubles; real-model behaviour (refusals, latency, cost per workflow) is unknown until
   the verifier runs with a key.
2. **Gmail OAuth app verification.** Google requires app verification before non-test users can grant
   `gmail.readonly`. Until then only listed test users can connect.
3. **WhatsApp 24-hour window.** Free-form notifications are only delivered inside Meta's customer-service
   window; production use needs approved message templates (not built).
4. **E2E depends on a live public job board** (Ramp on Ashby). Board changes or outages will fail CI
   independently of JobPilot.
5. **Single API instance** until object storage replaces local file storage.
6. **Resume parsing accuracy** has only been measured on synthetic resumes.
7. **Performance** has not been load-tested; nonce CSP makes every page server-rendered.
