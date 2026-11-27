# JobPilot — Full Project Audit (Phase 1)

> **Status update, 2026-11-27 (Phase 2):**
> * **Fixed:** D1–D4, D14, S2 and S3 (see the "Status" notes below and §11).
> * **Policy now in force:**
>   * the salary threshold is user-configurable (default ₹8 LPA), and a verified minimum must be strictly
>     greater than it;
>   * ₹6–12 LPA is rejected at the default threshold;
>   * unknown or ambiguous currency goes to manual review;
>   * daily limit 1–10,000;
>   * approvals are human-only.
>
> Phase 3 items (D5–D13) are untouched.

Date: 2026-11-27 · Commit audited: `4302d36` (main, CI green) · Auditor: Claude (Opus 5.5)

## 0. Method and what was touched

Audit only. **No application code, migration or configuration was changed.**

* **Code:** read the parsing, profile, discovery, matching, salary policy, tailoring, answers, approval and
  submission, integrations, worker, security and MCP code.
* **Tests:** ran every suite (results in §7).
* **Probes:**
  * Behaviour probes with synthetic data: a temporary test file in an isolated in-memory database. It was
    deleted afterwards and `git status` was clean.
  * Read-only `SELECT` queries on the local dev database. Only counts and integration status were read; no
    personal fields, tokens or account names.
  * One unauthenticated, read-only GET each to the public Greenhouse (`gitlab`), Lever (`leverdemo`) and
    Ashby (`ramp`) board APIs, plus one non-existent board to check error handling.
* **Not done:** no messages sent, no drafts created, no applications submitted, no credentials printed.
  `verify_integrations --live` was **not** run against your Gmail.

Terminology:

| Term | Meaning |
|---|---|
| Implemented | Code exists |
| Configured | Credentials present |
| Connected | Account authorised |
| Tested | Exercised against the live service |
| Production-ready | Safe to rely on for real users |

## 1. Working features (evidence-backed)

| Feature | Implementation | Tests / evidence |
|---|---|---|
| Auth, sessions, CSRF, revocation, API tokens | `api/auth.py`, `security/core.py`, `api/deps.py` | `test_api_security.py` (21) |
| Resume upload and validation (DOCX/PDF, magic bytes, macro and zip-bomb checks, streamed size limit) | `api/resumes.py`, `security/uploads.py` | `test_api_security.py`, `test_workflows.py` |
| Resume parsing and section classification (content-aware, confidence) | `domain/resume_parser.py` | `test_resume_domain.py` (13), `test_resume_classification.py` (21). **Synthetic resumes only** |
| Resume Review & Editor (correct, move, split, merge, re-extract) | `services/resume_review.py`, `api/resume_review.py`, `components/resume/review-editor.tsx` | `test_resume_review.py` (14), E2E |
| Corrected profile used by matching, answers and tailoring | `services/profile.py`, `services/applications.py`, `services/resumes.py` | `test_tailoring_matching_and_answers_use_the_corrected_profile` |
| Formatting-preserving DOCX tailoring with fabrication guard | `domain/docx_document.py`, `services/resumes.py`, `ai/guards.py` | `test_resume_domain.py`, `test_ai_features.py` |
| Ashby discovery | `integrations/sources/providers.py` | E2E (live `ramp` board); 12 Ashby sources synced OK in the dev DB; live probe 183 jobs |
| Approval invariant (snapshot hash; any edit voids approval; MCP and automations can't submit) | `services/applications.py` | `test_workflows.py` |
| Assisted submission, idempotent start, daily limit (rolling 24 h) | `services/applications.py::record_submission` | `test_workflows.py` |
| Application tracking, events, notes, interviews, follow-up reminder | `services/applications.py`, `services/interviews.py` | `test_workflows.py` |
| In-app notifications and daily summaries | `services/audit.py`, `worker/main.py` | `test_workflows.py` |
| Rate limiting (Redis, fail-closed for auth) | `security/rate_limit.py` | `test_rate_limit.py` (with real Redis) |
| Backups and restore drill; migrations round-trip | `ops/backup.py`, `alembic/` | `test_backup.py`, `test_migrations.py` |
| MCP server: 17 tools, 3 resources, 3 prompts, scoped token, loopback-only HTTP | `mcp-server/jobpilot_mcp.py` | `mcp-server/test_mcp.py` (7) |
| CSP, HSTS, accessibility | `frontend/src/proxy.ts` | E2E (18): zero CSP violations, axe on every page |

## 2. Partially working features

| Feature | What works | What doesn't (evidence) | Files |
|---|---|---|---|
| **Greenhouse discovery** | Live fetch works: GitLab returned 241 jobs, all with descriptions, https URLs and dates; 404 handled | **Employment type detected for 3 of 241** jobs although all 241 descriptions say "full-time" (type is inferred from the title only). **Salary extracted for 0 of 241**, though 120 mention pay (e.g. `zł 248,000 - zł 372,000`; PLN isn't recognised). Experience stated for only 13 of 241. **Adapter coverage 24%**; all tests use a fake adapter | `integrations/sources/providers.py`, `domain/job_parsing.py` |
| **Lever discovery** | Live fetch works: `leverdemo` returned 10 jobs, 4 with structured USD pay marked verified | Same title-only inference gaps; no tests against recorded Lever payloads | same |
| **Eligibility engine** | Salary policy, experience-gap and employment-type blocks, explanations, confidence | Skills relevance doesn't affect eligibility (a sales job with 0 matching skills is "eligible"). No location or work-arrangement eligibility. No per-decision evidence quotes from the listing. Policy deviations: D1–D4 | `domain/matching.py`, `domain/salary_policy.py` |
| **Job filters** | Text, company, location, work mode, saved state, score, eligibility | D5–D8: salary, experience, date and employment filters wrongly hide jobs | `services/jobs.py::search` |
| **Deduplication** | Same posting across providers (company + normalised title + city) | Not measured against labelled data. Same-provider duplicates (one role per city) are kept as separate jobs. Fingerprint ignores remote vs. city variants | `services/jobs.py::upsert_jobs`, `job_parsing.fingerprint` |
| **Stale/expired listings** | Postings missing from a successful sync are closed | D9: an empty successful response closes the whole board. No age-based staleness | `services/jobs.py` |
| **Application answers** | Profile-backed standard answers; unknowns left blank; AI drafts flagged | AI path never tested live (no credentials) | `services/applications.py::prepare` |
| **Gmail** | OAuth connected in the dev DB; read, classify and acknowledge code contract-tested | Not verified by `verify_integrations --live`; see §9 | `integrations/gmail.py`, `services/communications.py` |
| **Daily application limit** | Default 10, enforced at submission start and by automations | User-configurable up to 100 (schema `le=100`); your policy says the limit is 10 (D4) | `schemas/profile.py`, `services/applications.py` |

## 3. Broken features (defects confirmed by probes or tests)

| # | Severity | Defect | Evidence |
|---|---|---|---|
| D1 | **High (policy)** — ✅ fixed in Phase 2 | A verified minimum **exactly ₹8.0 LPA is "eligible"**; your policy requires strictly greater than ₹8 LPA (`lo >= threshold`) | Probe A/B: 8.0–10 → eligible; 8.0–8.0 → eligible |
| D2 | High (policy) — ✅ fixed in Phase 2 (decision: reject) | A verified range whose minimum is below ₹8 LPA (e.g. 6–12 LPA) goes to manual review, not rejection. Your rule ("verified minimum must be > 8") implies rejection; **needs your decision** | Probe C |
| D3 | High — ✅ fixed in Phase 2 | Verified pay with **no currency** is treated as INR. Lever `salaryRange` can omit currency, so USD 90,000–120,000 could read as ₹0.9–1.2 L | Probe D: `currency=None` → eligible |
| D4 | Medium (policy) — ✅ resolved in Phase 2 (decision: 1–10,000, enforced by the API) | The daily limit can be raised to 100 in Settings | `SettingsIn.daily_application_limit: Field(ge=1, le=100)` |
| D5 | **High** | The **minimum-salary filter compares monthly pay to an annual threshold**: a verified ₹70k–90k/month job (₹8.4–10.8 LPA) is hidden at `min_salary_lpa=8` | Probe: "Backend Engineer II" missing from filtered results |
| D6 | High | The **experience-level filter hides every job that doesn't state experience**. On live GitLab data that is 228 of 241 jobs | Probe: Platform Engineer and Data Engineer hidden at `experience_levels=3-5` |
| D7 | High | The **"posted within N days" filter hides jobs with no posting date** | Probe: 0 results for `posted_within_days=7` when dates are missing |
| D8 | High | The **employment-type filter hides jobs with unknown type**. Combined with title-only inference, filtering "Full-time" on Greenhouse hides about 99% of jobs | Live: 3 of 241 typed; `search` uses `employment_type IN (...)` |
| D9 | High | **A successful but empty board response closes all of that board's jobs.** They disappear from discovery until the next non-empty sync | Probe: 5 visible → 0 after an empty fetch, `last_status: ok` |
| D10 | Medium | Salary text parsing misses common Indian and global formats: `₹8,00,000 per annum`, `Salary: 10 lakh`, `Rs. 25,000 per month`, `30k/month`, PLN/zł and other currencies | Probe table |
| D11 | Medium | Work-mode inference reads negations and mixed text wrongly: "This role is not remote" → remote; "remote-first company … 3 days onsite" → remote | Probe |
| D12 | Low | `Experience: 3 years` isn't recognised as an experience requirement | Probe |
| D13 | Low | Provider URLs aren't validated: a `javascript:` URL is stored and returned to the UI. React 19 and the strict CSP block it from running, but the server should reject non-http(s) URLs | Probe: `Data Engineer → javascript:alert(1)` |
| D14 | Medium (ops) — ✅ fixed in Phase 2 (backed up, verified, migrated) | **Local dev database `backend/data/jobpilot.db` is one migration behind** (`8b864ebccddf`, head `b7c65986ac58`). Running the local app on it will fail on the new review columns. **Not migrated, because it holds data (16 users, 15 resumes, 10 applications) that may be yours.** Run `cd backend && uv run alembic upgrade head` after backing it up | `alembic check`; `verify_integrations` → FAIL database |

## 4. Missing features

* **Job providers:** no LinkedIn, Naukri or Indeed. The PRD lists them only as "potential future
  integrations" that must comply with platform terms. No generic public career-page (JSON-LD) source.
* **Provider robustness:** no retry or backoff on network errors or 429s, and no scheduled re-sync backoff
  per source. Sync runs synchronously inside the request.
* **Eligibility:**
  * no structured per-decision evidence (quoted listing text);
  * no "missing or uncertain information" list per decision, only a list of unknown factors;
  * no location, work-arrangement or skills-relevance gate;
  * no separate seniority verdict.
* **Labelled data:** no labelled job dataset for measuring discovery precision and recall, and no labelled
  real-resume dataset.
* **Notifications:** email notifications (`notify_email` exists in the model, but nothing sends and no UI
  shows it).
* **Synthetic end-to-end harness:** nothing covers employer acknowledgement through Gmail in one flow
  (pieces are tested separately).
* **Unused code:** `matching.experience_level_for_years` is never called.

## 5. Security concerns

| # | Severity | Concern | Notes |
|---|---|---|---|
| S1 | Medium | `gmail.compose` lets the app **send** mail as well as create drafts. The code never calls send, but the granted permission allows it | No scope gives drafts-only access; document this, keep a test that no send endpoint is called, and consider making Gmail drafting optional |
| S2 | Medium — ✅ fixed in Phase 2 (tool removed; approvals human-only) | The MCP `approve_application` tool lets an AI client approve on the user's behalf (with the `approvals:write` scope). Text injected into a job description could steer an agent to approve | Approval doesn't submit, and submission stays web-only. Consider removing the tool or requiring a second confirmation |
| S3 | Medium — ✅ superseded (limit policy is 1–10,000, validated server-side) | Daily limit can be raised to 100 | See D4 |
| S4 | Low | Provider URLs aren't validated | See D13 |
| S5 | Low | The dev database is unencrypted SQLite and holds an encrypted Gmail refresh token; backups include it | The token is Fernet-sealed; protect the encryption key and backups |
| S6 | Info | Carried over from SECURITY.md: single API instance (local storage), no MFA or password reset, non-expiring API tokens, audit retention not automated | Unchanged |

Unchanged since the last security review (re-run, all passing):

* approval invariant;
* cross-user isolation (including the new review endpoints);
* CSRF;
* web-only routes;
* no secrets in logs.

## 6. Duplicate or unnecessary implementations

* Two experience-level tables with different "fresher" ranges: `matching.LEVEL_RANGES` (0–0.5 years) vs
  `services/jobs._LEVEL_EXP` (0–0). The matching score and the filter can disagree.
* Two seniority mappings: `job_parsing.seniority` and the map inside `matching._level_factor`.
* `POST /resumes/{id}/apply-to-profile` parses the document twice (`parsed_from_resume`, then
  `import_resume` re-reads it).
* `matching.experience_level_for_years` is unused.
* The Settings profile editor and the Resume Review editor both edit profile entries. That's intentional;
  both now preserve corrections, but there are two UIs for the same data.

## 7. Test coverage and results (actual runs, 2026-11-27)

| Suite | Result |
|---|---|
| Backend on PostgreSQL 17 + Redis 7 | **138 passed**, 0 failed; coverage **86%** |
| Backend on SQLite | 136 passed, 2 skipped (Redis-only) |
| MCP server | 7 passed |
| Frontend unit | 25 passed; `tsc` and `eslint` clean |
| E2E (production build, live Ashby board) | 18 passed (run on 2026-11-27 for the shortcut fix; also green on GitHub Actions for `4302d36`) |
| Lint | `ruff` clean |
| Dependency audit | `pip-audit` (backend, MCP): no known vulnerabilities; `npm audit`: 0 |
| `alembic check` on the default local DB | **FAIL**: dev database behind head (D14). Clean on the test databases |
| `verify_integrations` (offline) | 1 pass (Gmail config) · 1 fail (dev DB schema) · 4 blocked (Redis not set in this shell, AI, WhatsApp, live) |

Low coverage that matters:

| Module | Coverage | Why it matters |
|---|---|---|
| `integrations/sources/providers.py` | **24%** | The real Greenhouse, Lever and Ashby mapping is untested offline |
| `api/communications.py` | 43% | |
| `services/communications.py` | 71% | |
| `integrations/sources/base.py` | 72% | |

Existing tests pass, but none of D1–D13 are caught by any test, and the salary boundary at exactly ₹8 LPA
has no test.

## 8. Dependencies and integration blockers

| Blocker | Unblocks |
|---|---|
| AI provider credentials (`JOBPILOT_LLM_*`) | Live tailoring, answers, cover letters, assistant, interview feedback |
| WhatsApp Cloud API credentials | WhatsApp notifications |
| Render payment method and deploy approval | Staging |
| Your decisions on D2 and D4 | Phase 2 policy work |
| Backup and migration of the local dev DB (D14) | Running the app locally on your existing data |

## 9. Live integrations readiness

| Integration | Purpose | Implemented | Configured | Connected / OAuth | Tested against the live service | Read / write | Production-ready |
|---|---|---|---|---|---|---|---|
| **Gmail** | Read job-related mail, classify, record employer acknowledgements; create reply drafts | Yes | **Yes** (client ID and secret in `backend/.env`; redirect `http://localhost:3000/...`) | **Yes in the local dev DB**: OAuth completed, scopes granted, last sync 2026-11-26 21:14 UTC, 3 communications stored | **Partly**: a sync you started through the app ran. **Not** verified by `verify_integrations --live`; no draft created by me | Read + write (drafts; scope also permits sending, see S1) | **No** |
| **Greenhouse** (Job Board API) | Discover jobs on public company boards | Yes | No credentials needed | n/a | **Yes, read-only today** (GitLab, 241 jobs; 404 handled) | Read-only | **No**: extraction gaps (§2), 24% offline coverage, no retries |
| **Lever** (Postings API) | Discover jobs | Yes | No credentials needed | n/a | **Yes, read-only today** (`leverdemo`, 10 jobs) | Read-only | **No**: same gaps, no recorded-payload tests |
| **Ashby** (Posting API) | Discover jobs | Yes | No credentials needed | n/a | **Yes**: E2E on every CI run, 12 dev sources, 183 jobs today | Read-only | Closest; same parsing gaps for non-INR pay |
| **WhatsApp** (Meta Cloud API) | Notifications | Yes (`send_text`, token check, worker delivery states) | **No** | No | No | Write (send) | **No** |
| **Employer application portals** (Greenhouse/Lever/Workday apply flows) | Submitting applications | **Deliberately not implemented**: assisted submission only (you submit on the employer's site, JobPilot records it) | n/a | n/a | n/a | — | The assisted flow is tested; automated submission is out of scope by design |
| **LinkedIn** | Jobs / Easy Apply | **No** (only message *drafting* templates in Communications) | — | — | — | — | Not planned without an approved partner API |
| **Naukri** | Jobs | **No** | — | — | — | — | Not planned |
| **Indeed** | Jobs | **No** | — | — | — | — | Not planned |
| AI provider (Anthropic / OpenAI-compatible) | Tailoring, answers, assistant | Yes | **No** | n/a | Contract tests only | — | No |

Details:

* **Gmail**
  * Scopes: `gmail.readonly`, `gmail.compose`, `openid`, `email`.
  * Missing setup: `gmail.readonly` is a *restricted* scope, so non-test users need Google app verification.
    For broad use that includes an annual third-party security assessment, which is typically paid. The
    redirect URI must match the deployed domain.
  * Risks: mailbox content (classified job mail only is stored); the refresh token is encrypted at rest;
    compose permission (S1).
  * Cost: the API is free; verification of restricted scopes can cost money.
* **Greenhouse / Lever / Ashby**
  * Public, unauthenticated APIs, free and rate-limited. Their apply endpoints need the *employer's* API key,
    so they aren't usable for candidates. Correctly not implemented.
  * Risks: provider HTML is sanitised (nh3), and descriptions are wrapped as untrusted content for AI.
    URLs aren't validated (D13).
* **WhatsApp**
  * Needs a Meta app with WhatsApp Business Cloud API, a phone number ID, and a system-user token with
    `whatsapp_business_messaging`.
  * Free-form messages are only delivered within 24 hours of the user's last message; outside that, approved
    templates are needed (not built).
  * Cost: business-initiated (template) conversations are paid per message; the test number is free but
    limited.
  * Risk: notification text goes to Meta.
* **LinkedIn / Naukri / Indeed**
  * Based on my knowledge, to be confirmed by you: LinkedIn job and apply APIs are partner-only. Naukri
    offers recruiter-side paid products, not a candidate job API. Indeed's publisher job-search API isn't
    open to new integrators.
  * Scraping these sites would breach their terms and the PRD's non-goals ("Bypass job portal restrictions").

## 10. Recommended implementation order

1. **Phase 2 policy fixes first, with boundary tests** (small, high impact):
   * D1 strict ">₹8 LPA";
   * D3 unknown currency → manual review;
   * D2 and D4 after your decision;
   * add skills-relevance, location and seniority verdicts;
   * per-decision evidence and missing-information lists;
   * keep internship stipend rules separate.
2. **Phase 3 discovery correctness:**
   * D9 empty-fetch guard;
   * D5–D8 filter false negatives ("unknown" stays visible, monthly pay normalised);
   * better employment-type, salary and work-mode extraction (D10–D12);
   * URL validation (D13);
   * retries and backoff;
   * recorded-payload tests for Greenhouse, Lever and Ashby;
   * a labelled synthetic job set measuring precision and recall per filter.
3. **Phase 4 synthetic end-to-end harness:** upload → correction → discovery (fake boards) → eligibility →
   tailoring (scripted LLM) → answers → approval → mock submission → mock Gmail acknowledgement, with failure
   paths at each stage.
4. **Security follow-ups:** S1 (document, and test that no send happens), S2 (decide whether MCP approval
   stays), dead-code and duplicate-table cleanup.

Ops item, separate from the phases: back up and migrate the local dev DB (D14) when you choose.

## 11. Progress report

| Feature | Existing status | Changes made | Tests executed | Actual result | Remaining blockers |
|---|---|---|---|---|---|
| Phase 1 audit | — | `AUDIT_REPORT.md` added; no code changes | All suites (§7) plus probes | Suites green; 14 defects found (D1–D14) | Decisions on D2 and D4 |
| Eligibility engine (Phase 2) | Partial | See below | `tests/test_eligibility_policy.py` (70), updated `test_domain_rules.py` and `test_workflows.py`, MCP tests, E2E | All passing (see CHANGELOG / TESTING.md) | No exchange-rate snapshot configured |
| Discovery and filtering (Phase 3) | Partial | — | — | — | Not started |
| End-to-end workflow (Phase 4) | Pieces tested separately | — | — | — | Not started |

### Phase 2 details (2026-11-27)

**Database safety (D14):**
* Backup: `backend/data/backups/jobpilot-pre-b7c65986ac58-20261126T224005Z.db`, written with SQLite's online
  backup (includes WAL contents), mode `0600`.
* Verification: `PRAGMA integrity_check` = ok; 36 of 36 tables; 12 key counts identical to the live database
  (users 16, resumes 15, applications 10, jobs 183, experiences 44, education 15, projects 16, skills 347,
  approval_requests 6, integrations 1, audit_events 220).
* Migration: `alembic upgrade head` (8b864ebccddf → b7c65986ac58). Afterwards: integrity ok, the same 11
  record counts unchanged, `alembic check` clean. No user records were modified.

**Configurable threshold:**
* Stored in `user_settings.min_ctc_lpa` (default 8.0).
* Edited in Settings → Job policies (₹ LPA, annual CTC).
* API validation: 0 ≤ value ≤ 200, finite (NaN and Infinity rejected), rounded to 2 decimals.
* The maximum of ₹200 LPA (₹2 crore a year) is set to catch typos.
* Each eligibility decision stores `policy` (`min_ctc_lpa`, allowances, engine version, FX source and date).
  Saved or applied jobs keep that decision when the policy changes, until the user chooses Re-evaluate.

**Approval:**
* The MCP `approve_application` tool was removed; `request_approval` was added (creates a pending request only).
* `POST /approvals/{id}/decide` is web-session only.
* The service refuses decisions that don't come from the web session.
* The `approvals:write` scope was removed.
* The existing test that asserted token approval was rewritten to assert refusal.

**Phase 2 review fixes (2026-11-27):**
* Atomic daily-limit enforcement (row lock).
* Tolerant `GET /settings` with a normalisation report.
* City aliases.
* Skills: manual review unless explicitly mandatory skills are all missing.
* Manual review when the user has a preference the listing doesn't state.
* `jobs.py` docstring corrected.

Live GitLab sample (242 listings, synthetic candidate), before → after:

| Rejection cause | Before | After |
|---|---|---|
| Skills | 158 | 1 |
| City mismatch | 21 | 0 |

The remaining rejections are seniority (51), experience (5) and employment type (3).

**Existing users' daily limit (owner decision, 2026-11-27):** data migration `c4d2e8f1a9b3` raises limits still
on the old default of 10 to 10,000; other saved values are kept.

* Backup first: `backend/data/backups/jobpilot-pre-c4d2e8f1a9b3-20261126T233031Z.db`, mode `0600`.
* Backup verification: integrity check ok; counts identical to the live database.
* After migrating: 16 of 16 users at 10,000; users 16, resumes 15, applications 10, jobs 183, skills 347,
  approval_requests 6 and audit_events 220 unchanged; integrity ok; `alembic check` clean.

## Phase 3 — M1: sync safety and board reliability (2026-11-27, uncommitted until approved)

| Defect | Before | After | Test |
|---|---|---|---|
| P1 empty response closes all jobs | 2 visible → 0, status "ok" | Nothing closed, status "warning" with explanation; baseline count kept | `test_empty_response_after_listings_closes_nothing` |
| P2 malformed listing crashes sync | Unhandled `KeyError`/`TypeError` | Listing skipped with a reason; malformed responses → board error; adapter exceptions caught (HTTP 200, status "error") | contract + `test_board_errors_and_adapter_crashes_change_nothing` |
| P3 no retries | 1 attempt | 3 attempts, full-jitter backoff, Retry-After honoured (≤30 s), 45 s budget, no retry on 4xx | retry tests |
| P4 temporary URL loss closes the job | 2 → 1 | Kept open; status "partial" | `test_invalid_listing_is_skipped_and_not_closed`, `test_temporary_url_loss_in_a_legacy_adapter_keeps_the_job` |
| P5 duplicate ids processed twice | 2 jobs from 1 id | 1 record; duplicate reported as skipped | `test_duplicate_ids_create_one_record`, contract |
| P6 Lever pay without currency "verified" | Currency `None`, verified | Re-assessed: the *amounts* are verified (structured ATS field), so the label stays. The currency is now normalised (`usd` → USD) and missing currency stays unknown, so eligibility returns manual review (`SAL_CURRENCY_UNKNOWN`) | `test_lever_contract`, `test_lever_pay_without_currency_goes_to_manual_review` |
| P15 unsafe URLs stored | `javascript:` stored | Only absolute http(s) URLs; invalid job URL → listing skipped, invalid apply URL → dropped | `test_safe_url`, contracts |
| Suspicious drop | — | Fewer than half of the last trusted count (boards with 10+ listings) → nothing closed, "warning" | `test_suspicious_drop_closes_nothing_but_a_normal_drop_does` |
| Saved/applied decisions | Recomputed silently when the profile or listing changed (same policy) | Never recomputed automatically; kept and marked outdated with reasons (policy, engine, profile, listing) | `test_saved_decision_is_kept_and_marked_outdated_when_the_listing_changes` |

Coverage: `providers.py` 24% → 97%, `base.py` 72% → 94%.
