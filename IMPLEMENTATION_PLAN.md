# JobPilot — Implementation Plan, PRD Audit & Acceptance

Legend: ✅ PASS (implemented and verified) · 🟡 PARTIAL · ⛔ BLOCKED (implemented, live verification needs credentials) · ❌ NOT IMPLEMENTED

Current milestone: **V1.2 — Staging & release validation** (2026-11-26), see §4. Previous: **V1.1 — Integration
verification & production readiness** (2026-11-26). No new product
features were added. The fixes in this milestone are security hardening and defects in existing behaviour.
The release-readiness report is in [RELEASE_READINESS_V1_1.md](RELEASE_READINESS_V1_1.md).

## 1. PRD audit (functional requirements)

| PRD ref | Requirement | Status | Evidence / gap |
|---|---|---|---|
| FR-001 | Account creation & secure auth | ✅ | Argon2, sessions with server-side revocation, CSRF, rate limits (tests) |
| FR-002 | Profile creation (all listed fields) | ✅ | Onboarding + Settings → Profile |
| FR-003 | PDF & DOCX upload | ✅ | Upload validation tests, E2E |
| FR-004 | **AI** profile extraction | 🟡 | Extraction is deterministic (sections, entities, skills). LLM-assisted extraction is not built |
| FR-005 | Verify before trusted | ✅ | Imported items unverified until confirmed; match explanations flag unverified skills |
| FR-006 | Separate experience categories | ✅ | Professional / internship / freelance / academic / personal kept separate (tests) |
| FR-007 | Edit all extracted information | 🟡 | Experiences, skills and profile fields are editable; projects, education and certifications can be added and removed but not edited in place |
| FR-008 | Profile completeness | ✅ | Completeness % and missing checks |
| FR-009 | Search by title, company, skills, location, industry, experience, employment | 🟡 | All except: the skills filter exists in the API but not the UI; industry isn't provided by the job-board APIs |
| FR-010–012 | Experience, location, salary filters | ✅ | Tests + UI |
| FR-013–015 | Dedup, normalisation, detail view | ✅ | Cross-source fingerprint, unified schema, detail sheet |
| FR-016–017 | Save/dismiss, scheduled discovery | ✅ | Tests; worker scheduling idempotent |
| §7 Sources | Greenhouse, Lever, public career pages, other permitted APIs | 🟡 | Greenhouse ✅ Lever ✅ Ashby ✅ (live). **Generic public career pages ❌** |
| FR-018–023 | Semantic matching, score, explanation, eligibility, skill gaps, recommendations | ✅ | Deterministic, reproducible, confidence-weighted (tests) |
| FR-024–029 | Parsing, sections, skills, experience, projects, quality | ✅ | Tests on synthetic resumes. **No labelled real-world dataset** (see §2) |
| FR-030 | Job-specific tailoring | ✅ / ⛔ | Reorder-only tailoring works without AI. AI rewording is implemented and contract-tested, but its live run is BLOCKED |
| FR-031 | Factual accuracy | ✅ | Fabrication guard + required acknowledgement (tests) |
| FR-032 | Formatting preservation | ✅ | Byte-level package comparison on generated templates. Not yet on a corpus of real-world templates |
| FR-033–035 | Comparison, versions, manual edit | ✅ | Side-by-side diff, versions, accept/reject/edit |
| FR-036–037, 039–042 | Preparation, resume selection, answers, unknowns, preview, approval | ✅ | Unknowns never invented (tests); approval snapshot invariant (tests) |
| FR-038 | Cover letter generation | ⛔ | Implemented and tested with a provider double; live BLOCKED (no AI key) |
| FR-043–046 | Pipeline, history, duplicates, submission verification | ✅ | Acknowledgement states; email-based acknowledgement tested with a mocked Gmail |
| FR-047 | Notes & reminders | 🟡 | Notes ✅. The follow-up date is set automatically (7 days) and editable via the API; no UI control to change it |
| FR-048–049 | Follow-up tracking, daily limit | ✅ | Tests |
| FR-050–055 | Assistant capabilities | ⛔ | Implemented, tool loop tested with doubles; live BLOCKED |
| FR-056–057 | Provider abstraction, cost monitoring | ✅ | Anthropic SDK + OpenAI-compatible contract tests; usage, budget, unpriced-model protection |
| FR-058–060, 062–064 | Job-specific prep, questions, evaluation, STAR, history | ✅ / ⛔ | Template + heuristic STAR mode verified. AI questions/feedback implemented; live BLOCKED |
| FR-061 | Conversational mock interviews | 🟡 | Written practice Q&A with feedback; not a turn-by-turn conversational mock |
| FR-065 | Gmail OAuth integration | ⛔ | OAuth, refresh, sync, drafts, revocation contract-tested; live BLOCKED (no Google client) |
| FR-066 | Email classification | ✅ | Rule classifier; unrelated mail never stored (tests) |
| FR-067–069 | Email/referral/LinkedIn drafting | ✅ / ⛔ | Manual drafting + logging ✅; AI generation live BLOCKED |
| FR-070 | WhatsApp notifications | ⛔ | Adapter + worker delivery contract-tested; live BLOCKED (no Meta credentials) |
| FR-071 | Communication history | ✅ | Linked to applications |
| FR-072–078 | Automation config, execution, monitoring, pause/resume, failures, approval, audit | ✅ | Tests; automations never submit |
| FR-079–084 | Analytics & recommendations | ✅ | From stored records, definitions shown |
| FR-085–092 | MCP server, tools, resources, prompts, security | ✅ | 17 tools / 3 resources / 3 prompts; scoped through the API; loopback-only HTTP. **Tested with a generic MCP client handshake. Claude Desktop/Code configs documented; ChatGPT/Copilot not tested** |
| §18 | Dashboard | 🟡 | All widgets ✅; **not user-customizable** |
| §19 | Non-functional: performance | ❌ | No load/performance testing yet |
| §19 | Accessibility | ✅ | axe WCAG 2 A/AA on every page in E2E; keyboard navigation |
| §19 | Reliability: retries, idempotency, recovery | ✅ | Queue retries, idempotency keys, backup/restore drill |
| §25 | Export & deletion | ✅ | Tests |
| §25 | Audit retention policy | 🟡 | Audit log ✅; retention policy not defined or automated |
| §22 | Success-metric instrumentation | ❌ | Activation/engagement metrics are not instrumented |

## 2. Release acceptance criteria (PRD §27)

| Criterion | Status | Notes |
|---|---|---|
| Register & authenticate | ✅ PASS | |
| Complete profiles | ✅ PASS | |
| Resume upload works | ✅ PASS | |
| Extraction verified against a test dataset | 🟡 PARTIAL | Synthetic fixtures (3 naming styles + PDF) only; needs a labelled set of real resumes |
| Section classification handles naming conventions | ✅ PASS | |
| Tailoring preserves the original document | ✅ PASS | Generated templates; real-template corpus pending |
| Jobs imported from configured sources | ✅ PASS | Live Greenhouse/Lever/Ashby |
| Search & filters | ✅ PASS | |
| Explainable matching | ✅ PASS | |
| Application preparation | ✅ PASS | |
| Approval enforced | ✅ PASS | Re-verified on PostgreSQL |
| Reliable tracking | ✅ PASS | |
| Dashboard data is real | ✅ PASS | |
| Core workflows have automated tests | ✅ PASS | 143 tests |
| Security checks pass | ✅ PASS | Review findings 1–9 fixed; 10–15 open/accepted (SECURITY.md) |
| Error handling works | ✅ PASS | |
| Documentation complete | ✅ PASS | |
| Deployment instructions verified | 🟡 PARTIAL | Production build, migrations and backup/restore verified locally on PostgreSQL + Redis; no hosted deployment (awaiting approval) |
| Credential-dependent features identified, not marked complete | ✅ PASS | AI, Gmail, WhatsApp are BLOCKED |

## 3. V1.1 task acceptance

| # | Task | Status | Evidence |
|---|---|---|---|
| 1 | Audit against PRD | ✅ PASS | §1–2 above |
| 2 | Identify incomplete / partial / unverified | ✅ PASS | §1–2; RELEASE_READINESS_V1_1.md |
| 3 | Configure & verify the real AI provider | ⛔ BLOCKED | No credentials. Contract tests for both adapters pass; verifier ready (`verify_integrations --live`) |
| 4 | Verify assistant, rewording, cover letters, interview feedback | ⛔ BLOCKED (live) / ✅ PASS (offline) | All four paths tested with provider doubles; verifier runs them live once a key exists |
| 5 | Google OAuth + Gmail read/classify/draft | ⛔ BLOCKED (live) / ✅ PASS (offline) | OAuth URL, exchange, refresh, revocation, sync + classification + acknowledgement, drafts — contract-tested |
| 6 | WhatsApp with test credentials | ⛔ BLOCKED (live) / ✅ PASS (offline) | Request shape, error mapping, worker delivery states; verifier performs a read-only token check and sends only with an explicit flag |
| 7 | CI | ✅ PASS | `.github/workflows/ci.yml`; green on GitHub (run 45883858343, V1.2) |
| 8 | Content-Security-Policy | ✅ PASS | Nonce CSP; E2E asserts per-request nonce and zero violations on the production build |
| 9 | Shared rate limiter | ✅ PASS | Redis sliding window; multi-instance and failure-mode tests against real Redis |
| 10 | Security review | ✅ PASS | 9 findings fixed, 6 open/accepted with owners (SECURITY.md) |
| 11 | Migrations and backup/recovery | ✅ PASS | Up/check/down/up on SQLite and PostgreSQL 17; backup → wipe → restore drill with `pg_dump`/`pg_restore` and file checksums |
| 12 | Production deployment plan | ✅ PASS | DEPLOYMENT.md. **Not deployed — awaiting approval** |

## 4. V1.2 — Staging & release validation

| Phase | Task | Status | Evidence / what's needed |
|---|---|---|---|
| 1 | Inspect git status and structure | ✅ PASS | |
| 1 | `.gitignore` covers .env, credentials, OAuth tokens, DBs, resumes, backups, logs, artifacts | ✅ PASS | Expanded ignore rules; staged-file review |
| 1 | Secret scan | ✅ PASS | detect-secrets + key patterns: no real secrets. Found and fixed a placeholder-key acceptance bug (SECURITY #16) |
| 1 | Clean initial commit | ✅ PASS | Commits on `main`; tracked files re-checked before push |
| 2 | GitHub repository + remote | ✅ PASS | `origin` = github.com/mani1234765/thejobautomation. Its initial LICENSE commit was merged (`bce77be`), not overwritten |
| 2 | Push + remote CI run | ✅ PASS | Pushed with approval (no force). [Run 45883858343](https://github.com/mani1234765/thejobautomation/actions/runs/45883858343) on `bce77be`: success — dependency audit, backend (PG + Redis), frontend, MCP, E2E/axe/CSP all green; manual live job skipped |
| 3 | AI live (assistant, rewording, cover letter, interview feedback) | ⛔ BLOCKED | No provider credentials configured |
| 3 | Gmail OAuth + draft (not sent) | ⛔ BLOCKED | No Google OAuth client |
| 3 | WhatsApp config + test message | ⛔ BLOCKED | No Meta credentials; sending also needs explicit approval |
| 4 | Hosting comparison & recommendation | ✅ PASS | Render recommended (DEPLOYMENT.md §10) |
| 4 | Postgres, Redis, resume storage, worker, env vars | ✅ PASS (defined) | `render.yaml`, `backend/Dockerfile`; `render blueprints validate`: only `need_payment_info` (no schema errors); image not built locally (no Docker) |
| 4 | Trusted proxy hops | ✅ PASS | Hop-count client IP; Next.js pass-through measured; tests. Live hop count to confirm on staging |
| 4 | HTTPS + security headers | ✅ PASS | HSTS, nonce CSP, frame/sniff/referrer/permissions headers (E2E) |
| 4 | MCP HTTP authenticated | ✅ PASS | No MCP endpoint is deployed; local HTTP transport is loopback-only |
| 4 | Backups & recovery | ✅ PASS (local) | Drill passes on PostgreSQL 17; staging run pending deployment |
| 4 | Deploy staging | ⛔ BLOCKED | Needs a payment method on the Render workspace and explicit deployment approval |
| 5 | Staging acceptance | ⛔ BLOCKED | Depends on deployment. Locally: full automated suite green (§6 of RELEASE_READINESS_V1_2.md) |

## 5. Next milestones
1. **Credentials** (owner): AI key, Google OAuth client, WhatsApp Cloud API → run `verify_integrations --live`
   and flip the BLOCKED rows to PASS or FAIL.
2. Add `integration-verification` environment secrets on GitHub for the manual live job.
3. Object storage for resumes (unblocks multi-instance API); audit-retention purge job.
4. Close PARTIAL items: edit-in-place for projects/education/certifications, skills filter in UI, follow-up date
   control, a labelled resume dataset, real-world DOCX template corpus.
5. Performance/load test; MFA and password reset.
