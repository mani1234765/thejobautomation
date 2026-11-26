# JobPilot V1.2 — Staging & Release Validation Report

Date: 2026-11-27 · No new product features · Pushed to GitHub with approval. **Nothing deployed or sent; no
other external account changed.**

## Final status: **BLOCKED**

**PASS**: repository hygiene, secret scan, security fixes, the staging configuration, the full automated
suite, and now a **green CI run on GitHub**. Every remaining item needs an owner action: provider
credentials, a payment method on the Render workspace, and deployment approval. Production deployment decision: **NO-GO until staging acceptance passes.**

## 1. GitHub CI — actual result

**PASS.** `main` pushed to github.com/mani1234765/thejobautomation after re-checking tracked files (no `.env`,
credentials, tokens, databases, backups or real resumes). The repository's initial LICENSE commit was merged,
not force-overwritten. [Run 45883858343](https://github.com/mani1234765/thejobautomation/actions/runs/45883858343)
on `bce77be` (2026-11-26 19:28–19:32 UTC): **success**.

| Job | Result |
|---|---|
| Dependency audit | success |
| Backend (lint, migrations, tests on PostgreSQL + Redis) | success |
| Frontend (lint, types, unit tests, build) | success |
| MCP server | success |
| E2E + accessibility + CSP (production build) | success |
| Live integration verification (manual) | skipped (manual trigger, no secrets) |

## 2. Live integration verification

| Integration | Live result | Provider response recorded | Offline evidence (not live) |
|---|---|---|---|
| AI provider (assistant, resume rewording, cover letters, interview feedback) | **BLOCKED** — no `JOBPILOT_LLM_*` credentials | None (no call made) | Contract tests for the Anthropic SDK and OpenAI-compatible adapters; feature tests with provider doubles |
| Gmail OAuth, scopes, draft (not sent) | **BLOCKED** — no Google OAuth client | None | Contract tests: authorization URL scopes (`gmail.readonly`, `gmail.compose`, no send scope), exchange, refresh, revocation, sync, draft MIME |
| WhatsApp Cloud API | **BLOCKED** — no Meta credentials; a test send also needs your approval | None | Contract tests: request format, error mapping, worker delivery states |

Verifier output on this machine: `1 passed · 0 failed · 7 blocked`. The setup steps for each item are
printed by `python -m app.ops.verify_integrations` and listed in RELEASE_READINESS_V1_1.md.

## 3. Staging URL and architecture

**Not deployed — no staging URL.** Recommended platform: **Render** (comparison in DEPLOYMENT.md §10).
The planned topology is defined in `render.yaml`:

```
Internet ─TLS─▶ Render proxy ─▶ jobpilot-web (Next.js, public, /login health check)
                                    │  /api/* rewrite (X-Forwarded-For passed through unchanged)
                                    ▼
                               jobpilot-api (private service, Docker, 1 instance, 5 GB resume disk,
                                    │         pre-deploy: alembic upgrade head)
                 jobpilot-worker ───┤
                                    ▼
                      jobpilot-db (Postgres 17, private)   jobpilot-kv (Key Value, noeviction, private)
```

`render blueprints validate` (2026-11-27, workspace `mani's workspace`): **`valid: false`, only
`need_payment_info`** on all five resources (database, Key Value, private API, worker, web). No schema or
field errors were reported. Private services, workers and disks have no free tier, so a payment method is
required before the Blueprint can be applied. Still unverified: the first Docker image build (no Docker on
this machine).

## 4. Security findings (this milestone)

| Severity | Finding | Status |
|---|---|---|
| High | Production accepted the `.env.example` placeholder secret key | Fixed + test |
| Medium | uvicorn `--forwarded-allow-ips '*'` trusts the client-controlled left-most `X-Forwarded-For` entry (verified in source) | Fixed: hop-count client IP, `--no-proxy-headers`, tests |
| Low | No HSTS | Fixed: production `Strict-Transport-Security` (E2E) |
| Info | Secret scan of the commit | Clean |
| Info | Staging proxy hop count (expected 1) | To confirm on staging |

Carried over (SECURITY.md): single API instance due to local file storage; no MFA, email verification or
password reset; audit retention not automated; non-expiring API tokens.

Approval and security protections are unchanged; all approval and authorization tests pass on PostgreSQL.

## 5. Staging acceptance checklist (to run after deployment)

| Area | Check | Status |
|---|---|---|
| Health | `/api/v1/health/ready` → database ok, `redis:ok`, storage ok | BLOCKED (not deployed) |
| Proxy hops | Sign in from a known IP; `auth.login` audit `ip` equals it; spoofed `X-Forwarded-For` prefix ignored | BLOCKED |
| Headers | CSP with nonce, HSTS, frame/sniff headers on the HTTPS URL | BLOCKED |
| Journeys | Register, log in, onboard with a resume, follow a board, matching, tailor, prepare, approve, assisted-submission handoff (**no real application is submitted**) | BLOCKED |
| Files | Resume upload ≤ 8 MB accepted; larger → 413; original and tailored downloads | BLOCKED |
| Limits | Daily application limit; login limit shared across restarts (Redis) | BLOCKED |
| Audit | Audit events for auth, approvals, submissions | BLOCKED |
| Integrations | `verify_integrations --live` from a Render shell | BLOCKED |
| Backups | Logical backup + restore into a scratch DB; confirm Postgres plan retention | BLOCKED |

## 6. Automated test results (local, 2026-11-26)

| Suite | Environment | Result |
|---|---|---|
| Backend | PostgreSQL 17 + Redis 7 | **103 passed**, coverage 85% |
| Backend | SQLite | 101 passed, 2 skipped (Redis-only) |
| MCP server | — | 7 passed |
| Frontend unit | jsdom | 21 passed |
| E2E + axe + CSP + HSTS | Production build, live public job board | 16 passed |
| Lint / format / types / migration drift | ruff, eslint, tsc, `alembic check` | Clean |

## 7. Known limitations
* Single API instance, with a few seconds of downtime per deploy (Render disk constraint).
* E2E depends on a live public job board (Ramp on Ashby).
* Assisted submission only: JobPilot never submits to employers itself.
* WhatsApp free-form messages only arrive inside Meta's 24-hour window; templates aren't built.
* Gmail access is limited to listed test users until Google verifies the OAuth app.
* Resume parsing accuracy has only been measured on synthetic resumes; no load testing yet.

## 8. Remaining production blockers (in order)
1. **Credentials** in `.env` / Render Dashboard → `verify_integrations --live` with no FAIL.
2. **Render**: payment method on the workspace, deployment approval.
3. **Staging acceptance** (§5) all PASS, including the observed proxy hop count.
4. Backup retention confirmed on the chosen Postgres plan; restore drill on staging.
