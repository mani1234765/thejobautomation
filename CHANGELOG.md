# Changelog

## Unreleased — Phase 2: policy and eligibility engine

### Changed (policy)
- Salary eligibility is **strictly greater than** the user's threshold (default ₹8 LPA). A verified minimum
  at or below it, including ranges like ₹6–12 LPA, is rejected.
- The threshold is user-configurable in Settings (0–200 LPA, two decimals). It's stored per user and
  validated server-side.
- Currency is never assumed. Missing or ambiguous currency (including a bare "$") goes to manual review.
  USD, PLN (zł), EUR and others are converted only with a fresh exchange-rate snapshot
  (`JOBPILOT_FX_RATES_FILE`) that records its source and date; otherwise manual review.
- Daily application limit: 1–10,000, default 10,000, enforced by the API and clamped at submission.
- **Approvals are human-only.** The MCP `approve_application` tool and the `approvals:write` scope are
  removed; MCP can `request_approval`. Decisions require a signed-in web session.

### Added
- Explainable eligibility verdicts (ELIGIBLE / REJECTED / MANUAL_REVIEW) across salary or stipend, skills,
  experience, seniority, location and employment type. Each verdict includes reason codes, the deciding
  rules, quoted evidence from the listing and missing information. Shown in the job detail panel.
- Each decision stores the policy it was made under. Saved or applied jobs keep their historical decision
  when the policy changes until the user re-evaluates (`POST /jobs/{id}/reevaluate`, `POST /jobs/reevaluate`).

### Fixed
- Settings "Job policies" controls had no accessible names (axe).
- **Daily limit race:** concurrent submission starts could exceed the limit (check-then-insert). Starts
  now take a per-user row lock (`SELECT … FOR UPDATE` on `user_settings`) and re-read state under it.
  Verified with simultaneous PostgreSQL requests.
- `GET /settings` returned 500 when a stored value was out of range. Values are now normalised to the
  nearest valid one and reported (`normalized`); nothing is rewritten until the user saves.
  Enforcement uses the same effective values.
- City aliases (Bangalore = Bengaluru, Bombay = Mumbai, Gurgaon = Gurugram, …) and whole-word matching
  ("Pune" no longer matches "Puneet").
- Zero overlap with *extracted* skills is now manual review, not rejection. Rejection needs two or more
  skills the listing explicitly calls mandatory, all missing.
- When the user restricts location, work arrangement or employment type and the listing doesn't state it,
  the verdict is manual review (was eligible).

### Operations
- Local dev database backed up (`backend/data/backups/jobpilot-pre-b7c65986ac58-20261126T224005Z.db`) and
  migrated to `b7c65986ac58`; record counts verified unchanged.

## Unreleased — Resume Review & Editor

### Added
- **Resume Review & Editor** (Resume Studio → Review & edit): edit, add and delete entries in every extracted
  section (summary, experience, projects, education, skills, certifications); move entries between sections
  and categories; split entries that were merged and merge duplicates; assign content under unrecognised
  headings. Every change is committed before the UI shows it as saved.
- Confidence and reasons on every extracted entry; low-confidence and moved entries are flagged for review.
- Explicit re-extraction with a preview. Edited entries are kept as the user left them, deleted entries stay
  deleted, and new entries arrive flagged for review.

### Changed
- Section classification also reads entry content (degrees, institutions, grades, job titles, employers,
  date ranges, repository links, certification terms, summary prose), not only headings. Misfiled entries
  are moved and flagged; ambiguous ones are flagged but left in place.
- Tailoring takes paragraph sections and skills from the corrected profile and gives the model the reviewed
  profile; matching and application answers already read it.
- `POST /resumes/{id}/apply-to-profile` and the profile CRUD endpoints no longer destroy corrections
  (same request and response shape).

### Fixed
- DOCX page-header text was appended to the last section (e.g. read as a certification).
- Institution extraction when the degree comes first ("B.Tech in CSE, NIT Warangal").
- Timestamps from PostgreSQL sessions in non-UTC time zones were not normalised to UTC, shifting dashboard
  activity to the next day late in the UTC day.
- Backend tests could read a developer's `backend/.env`; they're now hermetic.

### Database
- Migration `b7c65986ac58` (additive, reversible): review columns on experiences, projects, education and
  certifications; `details` on education and certifications; `resumes.review`.

## 1.2.0 — 2026-11-26 (staging preparation; not deployed)

### Security
- Production rejects placeholder or low-variety `JOBPILOT_SECRET_KEY` values; `.env.example` no longer ships one.
- Client IPs come from `JOBPILOT_TRUSTED_PROXY_HOPS` (right-most appended entries) and uvicorn runs with
  `--no-proxy-headers`, replacing the spoofable `--forwarded-allow-ips '*'` pattern.
- HSTS on production responses.

### Added (operations)
- `render.yaml` Blueprint and `backend/Dockerfile` for Render staging (unapplied).
- Platform URL normalisation (`postgres://`, Redis host/port); web tier accepts the API host and port.
- Readiness probe reports resume-storage writability.
- Expanded `.gitignore`; initial local git commit (not pushed).

## 1.1.0 — 2026-11-26 (integration verification & production readiness)

No new product features.

### Security
- Fixed AI daily-budget bypass: unpriced models are charged at the highest known rate (`ai_usage.cost_estimated`);
  per-user model overrides are limited to `JOBPILOT_LLM_ALLOWED_MODELS`.
- Logout revokes the session server-side (`revoked_sessions`, purged by the worker after expiry).
- Uploads are streamed and cut off at the size limit (413).
- Shared Redis rate limiter (`JOBPILOT_REDIS_URL`, required in production); auth limits fail closed.
- Strict nonce-based Content-Security-Policy for the web app; lockdown CSP on API responses. To keep the CSP
  strict, the toast library was replaced, Zod runs jitless and the scroll-lock style gets the nonce.
- MCP HTTP transport restricted to loopback with DNS-rebinding protection; default port 8765.
- Account deletion requires the current password; production validates the Fernet encryption key at boot.

### Fixed
- The daily summary setting now produces summaries (worker); the in-app notification toggle, which had no
  effect, is shown as always on.

### Added (operations)
- `python -m app.ops.verify_integrations` (live provider verification; BLOCKED rows with setup steps).
- `python -m app.ops.backup create|verify|restore` with checksummed archives.
- `GET /api/v1/health/ready` readiness probe.
- GitHub Actions CI; DEPLOYMENT.md; RELEASE_READINESS_V1_1.md.
- Tests: integration contracts, AI features, verifier, Redis limiter, migrations round-trip, backup drill,
  CSP (unit + E2E). Backend suite runs on PostgreSQL + Redis.

## 0.1.0 — 2026-11-26

First implementation, built from scratch.

### Added
- **Backend** (FastAPI, SQLAlchemy 2, Alembic): 34-table schema with initial migration; Argon2 auth, session
  cookies with CSRF, scoped personal API tokens, rate limiting, audit log, notifications, data export and
  account deletion.
- **Resume intelligence**: PDF/DOCX parsing, semantic section detection, separate professional/internship/
  freelance experience, projects (academic/personal), education, certifications, 120+ skill taxonomy (aliases and related-skill edges) with
  evidence, quality review; format-preserving DOCX tailoring with versioning, review and fabrication guard.
- **Job discovery**: Greenhouse, Lever and Ashby public-API adapters, normalisation, cross-source dedup,
  closed-posting detection, search & filters, deterministic explainable matching, salary/stipend policy.
- **Applications**: state machine, preparation (profile-backed answers, AI drafts, cover letters), approval
  snapshots, assisted submission with acknowledgement tracking, daily limits, interviews, notes.
- **AI**: provider-neutral layer (Anthropic via official SDK with server-side refusal fallback; any
  OpenAI-compatible endpoint), usage/cost tracking, daily budget, career assistant with read-only tools.
- **Interview prep, communications** (Gmail OAuth, rule classification, drafts), **WhatsApp** notifications,
  **automation centre** with DB-backed idempotent worker, **dashboard & analytics** from stored records.
- **MCP server** (official SDK 2.x): 17 tools, 3 resources, 3 prompts, scoped through the API.
- **Frontend** (Next.js 16): app shell, ⌘K palette, onboarding, all 12 product areas, light/dark themes,
  responsive layouts; Vitest and Playwright (with axe) suites.

### Fixed during the UI audit
- Naive UTC timestamps displayed in local time → timezone-aware datetimes everywhere.
- Low-information postings outranking well-specified matches → confidence-weighted scoring (v2026.11.2).
- Generic skill-gap advice → gaps from matching jobs only, soft skills excluded.
- Accessibility: unlabeled settings inputs, prohibited ARIA on a decorative element.
