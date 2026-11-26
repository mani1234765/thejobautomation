# JobPilot — Architecture

Status: living document. Last revised 2026-11-26 (end of first build).

## 1. Starting point

The project directory contained only `prd.md` (an RTF-encoded PRD). No prior code, database or
dependency was preserved — this is a clean implementation.

## 2. System overview

```
┌────────────────────────┐      same-origin /api/*      ┌──────────────────────────────┐
│  frontend/ (Next.js)   │ ───────────────────────────▶ │  backend/ (FastAPI)          │
│  App Router, RSC shell │   httpOnly session cookie    │  api → services → models     │
│  React Query, shadcn   │ ◀─────────────────────────── │  integrations/ (adapters)    │
└────────────────────────┘                              │  ai/ (provider-neutral LLM)  │
                                                        └──────────┬───────────────────┘
┌────────────────────────┐   HTTPS + personal API token            │ SQLAlchemy 2 / Alembic
│  mcp-server/ (MCP SDK) │ ─────────────────────────────▶ backend  ▼
│  stdio / streamable    │                              ┌──────────────────────────────┐
└────────────────────────┘                              │ PostgreSQL (prod)            │
                                                        │ SQLite (local dev / tests)   │
┌────────────────────────┐   polls `tasks` table        └──────────────────────────────┘
│  worker (same codebase)│ ─────────────────────────────▶  (DB-backed queue + scheduler)
└────────────────────────┘
```

A **modular monolith**: one backend codebase deployed as two processes (API + worker), plus a
separately deployable MCP server that talks to the backend *only through its public API*. This
keeps a single place for authorization and approval rules — an MCP client can never reach the
database directly, so it cannot bypass them.

### Key decisions

| Decision | Options considered | Choice & trade-off |
|---|---|---|
| Background jobs | Celery+Redis, RQ, Arq, DB-backed queue | **DB-backed queue** (`tasks` table, `SELECT … FOR UPDATE SKIP LOCKED` on Postgres, single worker on SQLite). No extra infrastructure; durable; idempotency keys enforced by a unique constraint. Throughput is lower than Redis, which is irrelevant at per-user job-search volumes. Swappable later behind `TaskQueue`. |
| ORM mode | async vs sync SQLAlchemy | **Sync** SQLAlchemy 2.0 in FastAPI's threadpool. Simpler transactions, identical behaviour on SQLite and Postgres. |
| Auth | NextAuth, external IdP, first-party | **First-party**: Argon2id password hashes, short-lived signed session JWT in an httpOnly `SameSite=Lax` cookie, `token_version` for global logout, CSRF header check on mutations. Personal API tokens (hashed) for MCP. OAuth only for *integrations* (Gmail). |
| Frontend ↔ API | CORS cross-origin, Next proxy | **Next.js rewrite** `/api/* → backend`. Same origin, so cookies stay httpOnly and no CORS surface. |
| Match scoring | LLM score, embeddings, deterministic | **Deterministic, documented scoring** (PRD FR-019: "not arbitrary LLM output"; reproducible). Skill semantics come from a curated taxonomy with aliases and related-skill edges. The LLM is used for *explanations and tailoring*, never for the number. |
| Resume formatting preservation | Regenerate DOCX, template engine, in-place run editing | **In-place paragraph editing** on a copy of the original DOCX. Each paragraph (body, tables, headers/footers) gets a stable path id; accepted edits replace text while keeping the first run's `rPr` and the paragraph's `pPr`. Everything not edited is byte-identical XML. |
| LLM access | vendor SDKs vs raw HTTP | One `LLMProvider` protocol. **Anthropic via the official `anthropic` SDK** (structured outputs, tool use, server-side refusal fallback, explicit effort); **OpenAI-compatible endpoints via `httpx`** so any compatible gateway works without another SDK. Uniform timeouts, retries, usage accounting. |
| Job sources | scraping vs official feeds | **Official public job-board APIs only**: Greenhouse Job Board API, Lever Postings API, Ashby Posting API. LinkedIn/Naukri/Indeed/Glassdoor are *not* integrated: none offers a permitted candidate-side API. |
| Submission | browser automation vs provider APIs vs assisted | Candidate-side submission APIs require employer credentials. JobPilot ships **assisted submission**: after approval the user opens the official apply URL with every prepared artefact, then records the outcome. Status is `attempted` until acknowledged (user-confirmed or detected confirmation email). A `SubmissionAdapter` interface exists for employer-authorised API submission. We never claim a submission succeeded without acknowledgement. |

## 3. Backend layout

```
backend/
  app/
    main.py              FastAPI app factory, middleware, routers
    config.py            Pydantic Settings (env-driven, validated at boot)
    db.py                engine/session, transaction helpers
    models/              SQLAlchemy models (one module per domain)
    schemas/             Pydantic request/response contracts
    api/                 routers: auth, profile, resumes, jobs, applications, ...
    services/            business logic (no HTTP concerns)
    domain/              pure logic: taxonomy, matching, salary policy, state machine, parsing
    integrations/        job sources, gmail, whatsapp, submission adapters
    ai/                  provider protocol, openai/anthropic adapters, prompts, guards
    worker/              task queue, scheduler, task handlers
    security/            passwords, tokens, crypto, rate limit, uploads
  alembic/               migrations
  tests/
```

Rules: routers validate and authorize, then call a service. Services own transactions.
`domain/` is pure and exhaustively unit-tested. Every query is scoped by `user_id` through
service helpers (`get_owned(...)`) — there is no unscoped repository access from routers.

## 4. Data model

All primary keys are UUIDv4 strings (portable across SQLite/Postgres). Timestamps are UTC.
`(user_id, …)` indexes on every user-owned table.

| Table | Purpose / key columns |
|---|---|
| `users` | email (unique, lower), password_hash, token_version, onboarding_completed_at |
| `user_profiles` | 1:1 user. full_name, phone, location, work_authorization, objectives, target_titles (JSON), preferred_locations (JSON), work_modes, experience_levels, employment_types, years_experience, salary_expectation, verified_at |
| `user_settings` | 1:1 user. salary policy, stipend policy, daily_application_limit, approval_mode, ai provider/model, notification prefs |
| `api_tokens` | personal tokens for MCP: token_hash (sha256), scopes, last_used_at, revoked_at |
| `resumes` | user_id, name, original filename, mime, storage_key, sha256, is_primary, parse_status, parsed_at, quality report (JSON) |
| `resume_versions` | resume_id, job_id?, kind (`original`/`tailored`), storage_key, status (`draft`/`approved`), change set (JSON) |
| `resume_sections` | resume_id, ordinal, raw_heading, canonical_type, confidence, text, paragraph_ids (JSON source refs) |
| `skills` | user_id, canonical name, category, source (`resume`/`user`), verified, evidence (JSON) — unique (user_id, canonical) |
| `experiences` | user_id, resume_id?, category (`professional`/`internship`/`freelance`), employer, title, location, start/end, is_current, bullets (JSON), verified |
| `projects` | user_id, category (`academic`/`personal`/`professional`), name, description, technologies (JSON), url, verified |
| `education` | user_id, institution, degree, field, start/end, grade, verified |
| `certifications` | user_id, name, issuer, issued_on, verified |
| `job_sources` | user_id, provider (`greenhouse`/`lever`/`ashby`), identifier (board token), display name, enabled, last_synced_at, last_status, last_error — unique (user_id, provider, identifier) |
| `companies` | normalized name (unique), display name, website, size, industry |
| `jobs` | shared catalogue. provider, external_id (unique together), source_key, company_id, title, normalized_title, location, work_mode, employment_type, is_internship, experience_min/max, salary_min/max/currency/period, salary_disclosure (`undisclosed`/`advertised`/`verified`), description_text, description_html (sanitized), required/preferred skills (JSON), url, apply_url, posted_at, fingerprint (dedup), first_seen_at, last_seen_at, closed_at |
| `job_matches` | user_id, job_id (unique together), score 0–100, breakdown (JSON), eligibility (JSON), scoring_version, computed_at |
| `saved_jobs` | user_id, job_id (unique together), state (`saved`/`dismissed`), note |
| `applications` | user_id, job_id (unique together = duplicate prevention), status, resume_version_id, cover_letter, submission_state (`not_submitted`/`attempted`/`acknowledged`/`failed`/`unknown`), idempotency_key (unique), submitted_at, acknowledged_at, follow_up_at, notes |
| `application_events` | append-only history: from_status, to_status, actor (`user`/`system`/`mcp`/`automation`), detail |
| `application_documents` | application_id, kind (`resume`/`cover_letter`/`other`), resume_version_id?, storage_key |
| `application_answers` | application_id, question, answer, confidence (`verified`/`inferred`/`unknown`), needs_review, evidence |
| `approval_requests` | user_id, application_id, status (`pending`/`approved`/`rejected`/`expired`), requested_by, decided_at, decided_via, scheduled_for |
| `automation_configs` | user_id, name, kind (`discovery`/`preparation`), schedule (interval minutes), filters (JSON), state (`active`/`paused`/`stopped`), next_run_at |
| `automation_executions` | config_id, status, started/finished, stats (JSON), log (JSON structured lines), error |
| `tasks` | queue: kind, payload, user_id, status, attempts, max_attempts, run_after, idempotency_key (unique), locked_at, last_error |
| `notifications` | user_id, kind, title, body, link, read_at, channel deliveries (JSON) |
| `communications` | user_id, application_id?, channel, external_id (unique per user), direction, sender, subject, snippet, category, classified_by, received_at |
| `interviews` | user_id, application_id, stage, scheduled_at, mode, notes |
| `interview_sessions` | user_id, job_id?, kind (`plan`/`mock`), questions (JSON), transcript (JSON), feedback (JSON), progress |
| `ai_conversations` / `ai_messages` | assistant threads; messages include role, content, tool calls |
| `ai_usage` | user_id, provider, model, feature, input/output tokens, cost_usd, latency_ms, success |
| `integrations` | user_id, provider (`gmail`/`whatsapp`), status, encrypted credentials (Fernet), scopes, connected_at, last_error |
| `audit_events` | user_id, actor, action, entity_type, entity_id, metadata (redacted JSON), ip, created_at |

### Application state machine

```
discovered → shortlisted → preparing → awaiting_approval → approved → submitted → assessment → interview → offer
                                    ↖ (rejected approval / edits)        ↘ failed (retryable → approved)
any non-terminal → withdrawn;   submitted/assessment/interview → rejected
```
Transitions are validated in `domain/application_state.py`; every transition writes an
`application_events` row and an audit event in the same transaction. `submitted` requires an
approved `approval_requests` row — enforced in the service, not the UI.

## 5. API surface (v1, prefix `/api/v1`)

| Area | Endpoints |
|---|---|
| auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/logout-all`, `POST/GET/DELETE /auth/tokens` |
| profile | `GET/PUT /profile`, `GET/PUT /settings`, `POST /profile/verify`, `POST /onboarding/complete`, CRUD `/profile/{experiences,projects,education,skills,certifications}` |
| resumes | `POST /resumes` (multipart), `GET /resumes`, `GET /resumes/{id}`, `POST /resumes/{id}/parse`, `POST /resumes/{id}/apply-to-profile`, `GET /resumes/{id}/quality`, `POST /resumes/{id}/tailor` (job), `GET/PUT /resume-versions/{id}`, `POST /resume-versions/{id}/approve`, `GET /resume-versions/{id}/download` |
| jobs | `GET /jobs` (search + filters + match), `GET /jobs/{id}`, `POST /jobs/{id}/save`, `POST /jobs/{id}/dismiss`, `GET /jobs/compare?ids=`, `GET/POST/DELETE /job-sources`, `POST /job-sources/{id}/sync` |
| applications | `GET/POST /applications`, `GET /applications/{id}`, `POST /applications/{id}/transition`, `POST /applications/{id}/prepare`, `PUT /applications/{id}/answers/{aid}`, `POST /applications/{id}/request-approval`, `POST /approvals/{id}/decide`, `POST /applications/{id}/submission` (record attempt/ack/failure) |
| assistant | `GET/POST /assistant/conversations`, `POST /assistant/conversations/{id}/messages` |
| interviews | `GET/POST /interview/sessions`, `POST /interview/sessions/{id}/answer` |
| comms | `GET /communications`, `POST /communications/draft`, `POST /integrations/gmail/*` |
| automation | CRUD `/automations`, `POST /automations/{id}/{pause,resume,stop,run}`, `GET /automations/executions` |
| insights | `GET /dashboard`, `GET /analytics?from&to`, `GET /notifications`, `POST /notifications/read` |
| system | `GET /health`, `GET /ai/status`, `GET /export` |

OpenAPI docs are served at `/api/docs` in development.

## 6. AI architecture

* `ai/provider.py` — `LLMProvider` protocol: `complete(messages, schema?, tools?) -> LLMResult`
  with token usage. Adapters: `anthropic` (official SDK; `output_config` JSON-schema outputs and effort;
  `fallbacks: "default"` on models that support it; assistant turns are replayed with their exact raw
  content blocks, append-only) and `openai` (any OpenAI-compatible base URL, `response_format` json_schema).
  Provider, model, base URL and key are environment-driven; no provider configured ⇒ AI
  features return an explicit `ai_unavailable` state (never canned text).
* Structured output: JSON-schema-constrained responses validated by Pydantic; one repair retry.
* Reliability: timeout, exponential backoff on 429/5xx, max-token caps, per-user daily budget.
* Cost: every call writes `ai_usage` (tokens, model, estimated cost from a price table).
* Prompt-injection defence: untrusted content (JDs, resumes, emails) is wrapped in delimited
  `<untrusted_document>` blocks with an explicit system rule that it is data; tools exposed to
  the assistant are read-only or *draft-only* — anything consequential (submit, send) creates an
  approval request the user must decide in the UI.
* Matching is deterministic (`domain/matching.py`, scoring v2026.11.2): weighted factors, unknown
  factors excluded from the denominator, and the result shrunk toward 50 by the share of unknown weight.
* Fabrication guard (`ai/guards.py`): tailored text is checked against the candidate's evidence —
  any skill from the taxonomy or any number that does not appear in the source resume/profile is
  flagged and the suggestion is blocked from auto-acceptance.

## 7. Frontend architecture

* Next.js App Router, TypeScript strict, Tailwind v4, shadcn/ui primitives, Framer Motion for
  restrained transitions, lucide icons, React Query for server state, React Hook Form + Zod.
* Route groups: `(marketing)` landing, `(auth)` login/register, `(app)` authenticated shell with
  sidebar + command palette (⌘K).
* `lib/api.ts` — typed fetch client (CSRF header, error normalisation); `lib/queries/*` React
  Query hooks per domain. No mock data in production code paths.

## 8. External dependencies & risks

| Dependency | Needs | Status if missing |
|---|---|---|
| LLM provider | `JOBPILOT_LLM_*` key | AI features show "Connect a provider"; deterministic parsing/matching still work |
| Greenhouse/Lever/Ashby | none (public) | — |
| Gmail | Google OAuth client id/secret | Integration card shows *Requires setup* |
| WhatsApp | Meta WhatsApp Cloud API token + phone id | *Requires setup* |
| PostgreSQL | `DATABASE_URL` | dev falls back to SQLite |

Risks: source rate limits (sync is throttled + cached), DOCX edge cases (tested against
generated templates with tables/headers/columns), LLM hallucination (guard + human review),
duplicate submissions (unique `(user_id, job_id)` + idempotency keys).

## 9. As built — notable implementation details

* **Timestamps**: every datetime column uses `UTCDateTime`, so values are timezone-aware on SQLite and
  PostgreSQL alike.
* **Worker**: `app/worker/main.py` loops: schedule due automations (idempotency key = config + slot), claim
  one task (`SKIP LOCKED` on Postgres), run it with retries/backoff, deliver pending WhatsApp notifications.
* **MCP server** (`mcp-server/jobpilot_mcp.py`, MCP SDK 2.x `MCPServer`): 17 tools, resources
  `jobpilot://profile`, `jobpilot://applications`, `jobpilot://jobs/{id}`, and 3 prompts; stdio or
  streamable HTTP. It holds only a personal token and calls the public API.
* **Frontend**: Next.js 16 with Cache Components off (client-rendered product screens), `proxy.ts` for
  optimistic auth redirects, `/api/*` rewritten to FastAPI.

## 10. V1.1 additions

* **Rate limiting**: `app/security/rate_limit.py` — Redis sliding window (Lua) shared by all API instances;
  in-process fallback for development.
* **CSP**: nonce generated per request in `frontend/src/proxy.ts`; the root layout reads it, so every route
  renders per request. Client libraries that inject styles receive the nonce (`get-nonce`).
* **Sessions**: JWT `jti` + `revoked_sessions` table for logout revocation.
* **Operations**: `app/ops/backup.py`, `app/ops/verify_integrations.py`, `/api/v1/health/ready`.
* **Migrations**: `alembic/env.py` renders app column types as plain SQLAlchemy types so migrations never
  import application code.
