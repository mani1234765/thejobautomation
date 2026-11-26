# Testing

## Suites

| Suite | Command | Count | What it proves |
|---|---|---|---|
| Backend domain | `uv run pytest tests/test_resume_domain.py tests/test_domain_rules.py` | 34 | Parsing across naming styles, experience categories, DOCX formatting preservation (package byte-identity), salary/stipend policy, state machine, JD parsing, taxonomy ambiguity, deterministic + confidence-weighted matching |
| Backend API & security | `uv run pytest tests/test_api_security.py` | 21 | Auth, httpOnly cookies, CSRF, rate limits, cross-user isolation, token scopes, web-only routes, logout-all, **server-side logout revocation**, upload validation + **streamed size limit**, approval can't be disabled, **API CSP**, export, **re-authenticated** deletion, model allow-list, production key validation, readiness probe |
| Resume classification | `uv run pytest tests/test_resume_classification.py` | 21 | Varied headings; education, projects and summary misfiled under the wrong heading are moved and flagged; unrecognised headings classified by content or reported; ambiguous entries flagged, not moved; correctly labelled resumes not flagged |
| Resume Review & Editor | `uv run pytest tests/test_resume_review.py` | 14 | Edit/add/delete, move (education out of experience, category changes, to summary/skills), split and merge, validation, persistence across a restart, cross-user isolation, original file untouched, re-extraction keeps edits/moves/deletions, legacy rows adopted, tailoring/matching/answers read the corrected profile |
| Eligibility & policy (Phase 2) | `uv run pytest tests/test_eligibility_policy.py` | 70 | Salary boundaries (₹8 exactly, ₹8.01, ₹6–12, max-only, monthly), undisclosed/unverified, missing or ambiguous currency, INR/USD/PLN conversion, missing/stale/invalid rates, stipend separation, per-rule verdicts with quoted evidence, missing and conflicting information, threshold validation/persistence/restart/per-user, daily limit 1/10/9,999/10,000 and 10,001 rejected, token bypass, clamping, historical decisions and re-evaluation |
| Phase 2 review fixes | `uv run pytest tests/test_phase2_fixes.py` | 41 | **Simultaneous PostgreSQL submission starts never exceed the limit** (plus a control that fails without the lock), same-application double start, tolerant `GET /settings` with normalisation report, city aliases × work arrangements, whole-word city matching, skills review vs explicit-mandatory rejection, review when a preferred fact is missing |
| Backend workflows | `uv run pytest tests/test_workflows.py` | 17 | Resume → profile, discovery, tailoring, application lifecycle, daily limit, MCP can't bypass approval, automations, assistant loop, AI budget, interviews, dashboard/analytics, **daily summary** |
| AI features | `uv run pytest tests/test_ai_features.py` | 5 | Cover letters, drafted vs unknown answers, honest degradation on provider failure, interview plan + feedback, prompt-injection containment, budget for unpriced models |
| Integration contracts | `uv run pytest tests/test_integration_contracts.py` | 12 | Exact requests/responses for Anthropic (official SDK via mock transport), OpenAI-compatible, Google OAuth/Gmail (exchange, refresh, list, draft, revocation, **full sync with classification + acknowledgement**), WhatsApp (request, errors, worker delivery states) |
| Integration verifier | `uv run pytest tests/test_verify_integrations.py` | 5 | The live verifier itself: BLOCKED without credentials, PASS/FAIL logic for AI, Gmail and WhatsApp, side-effect gating |
| Infrastructure | `uv run pytest tests/test_rate_limit.py tests/test_migrations.py tests/test_backup.py` | 9 | Redis limiter shared across instances, window expiry, fail-closed/fail-open, production requires Redis; migrations up → check → down → up; **backup → wipe → restore drill** with checksums and tamper detection |
| MCP server | `cd mcp-server && uv run pytest` | 8 | Tool registry (no submit/send), token forwarding, error mapping, input validation, loopback-only HTTP, no token leakage |
| Frontend unit | `cd frontend && npm test` | 25 | Formatting, diff, API client (CSRF, errors), components, **CSP builder**, toast store, **command-menu shortcut (⌘K and Ctrl+K)** |
| E2E | `cd frontend && npx playwright test` | 19 | Full journey on a real public job board, **resume review: edit, move, reload, re-extract**, **command menu via the platform shortcut and Ctrl+K**, axe on every page, **CSP header per request + zero CSP violations** |

Backend total: **250** (all pass on PostgreSQL 17 + Redis 7, coverage 87%); on SQLite without Redis, 244 pass and
6 skip (2 Redis-only, 4 PostgreSQL-only concurrency tests). Grand total **302** automated tests (250 backend,
8 MCP, 25 frontend, 19 E2E).

### Running against real PostgreSQL and Redis
```bash
export JOBPILOT_TEST_DATABASE_URL=postgresql+psycopg://localhost/jobpilot_test
export JOBPILOT_TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://localhost/jobpilot_migrations
export JOBPILOT_TEST_BACKUP_SOURCE_URL=postgresql+psycopg://localhost/jobpilot_backup_src
export JOBPILOT_TEST_BACKUP_TARGET_URL=postgresql+psycopg://localhost/jobpilot_backup_dst
export JOBPILOT_TEST_REDIS_URL=redis://localhost:6379/15 JOBPILOT_REDIS_URL=redis://localhost:6379/15
uv run pytest            # these databases are dropped and recreated by the tests — use disposable ones
```

### E2E against a production build
```bash
cd backend && uv run alembic upgrade head && uv run uvicorn app.main:app --port 8000 &
cd frontend && npm run build && npm start -- -p 3000 &
npx playwright test     # the CSP test expects production headers; in dev the policy also allows eval
```

## CI (`.github/workflows/ci.yml`)
Jobs: **backend** (ruff, `alembic upgrade/check/downgrade` on PostgreSQL, pytest on PostgreSQL + Redis with
coverage, pytest on SQLite) · **mcp** · **frontend** (lint, typecheck, Vitest, build) · **audit** (`pip-audit`
for both Python projects, `npm audit`) · **e2e** (PostgreSQL + Redis + production build + Playwright) ·
**live-integrations** (manual `workflow_dispatch`, runs the live verifier with repository secrets in a
protected environment).

The workflow passes `actionlint`, and every job's commands were run locally on this machine with
PostgreSQL 17 and Redis 7. It has **not yet run on GitHub**: the repository has no remote.

## Live integration verification
`python -m app.ops.verify_integrations [--live]` — see its `--help`. Without credentials every live row is
**BLOCKED** with setup steps. Mocked contract tests are *not* evidence that a provider accepts real
credentials; only this command, run with real credentials, is.

## Test doubles
Test doubles live only in `backend/tests/` (`FakeAdapter`, `ScriptedLLM`, `ShapeAwareLLM`, mock HTTP
transports). Production code never imports them.

## Not covered
* Live AI, Gmail and WhatsApp (no credentials — BLOCKED).
* ChatGPT Desktop / GitHub Copilot as MCP clients.
* Load and performance testing.
* A labelled real-world resume dataset for extraction accuracy.
* Visual-regression snapshots.
