# JobPilot Security Model

Last reviewed: 2026-11-26 (V1.1 integration & production-readiness review).

## Authentication & sessions
* Passwords: Argon2id. Minimum 10 characters mixing at least two character classes. Responses and timing
  are identical for unknown emails and wrong passwords.
* Sessions: HS256 JWT (`sub`, `ver`, `jti`, `exp`) in an `httpOnly`, `SameSite=Lax` cookie (`Secure` required
  in production), 7-day TTL.
  * **Logout revokes the session server-side** (its `jti` goes into `revoked_sessions` until expiry), so a
    copied cookie stops working immediately.
  * "Sign out everywhere" and password changes bump `token_version`, which invalidates every session.
* CSRF: double-submit token bound to the session by HMAC with the server secret (`jp_csrf` cookie →
  `x-jobpilot-csrf` header), required on every cookie-authenticated mutation. It is stateless, so it works
  across instances.
* Re-authentication: password change and account deletion require the current password.
* Personal API tokens (MCP): random 256-bit, shown once, stored as SHA-256. They are scoped, revocable, and
  their last use is tracked.

## Authorization
* Every user-owned row is fetched through `get_owned()`, which returns 404 (never 403) for other users' rows.
* Jobs are a shared catalogue, visible only through boards the user follows.
* **Web-only actions** can never be performed with an API token: profile/settings changes, uploads, answer
  edits, **recording a submission**, integrations, export, deletion.
* **Approval is a server invariant.** `submitted` is reachable only through the submission service, which
  requires an approved `ApprovalRequest` whose snapshot hash still matches the application. Any later edit
  voids the approval. Automations and MCP clients cannot submit. `require_approval` cannot be disabled.
  The V1.1 review re-ran every approval test on PostgreSQL; no protection was changed or weakened.

## Rate limiting
* Sliding-window limiter in **Redis** (`JOBPILOT_REDIS_URL`, required in production), atomic via Lua, so it is
  shared by every API instance.
  * Auth: 10/min per IP and per email, **fail-closed** if Redis is unreachable.
  * API: 240/min per user, fail-open with an error log.
* The in-process limiter is used only when no Redis URL is configured (development/tests).
* Per-IP limits depend on correct proxy trust: run uvicorn with `--forwarded-allow-ips` set to your proxy
  hops only (see DEPLOYMENT.md). With `*`, clients can spoof their IP; the per-email limit still applies.

## Content-Security-Policy
* Web: a fresh nonce per request (`src/proxy.ts`):
  * `script-src 'self' 'nonce-…' 'strict-dynamic'`, `style-src 'self' 'nonce-…'`.
  * `style-src-attr 'unsafe-inline'` — attributes only, needed for positioning and progress bars.
  * `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`,
    `upgrade-insecure-requests`.
  * Development additionally allows `'unsafe-eval'` and websocket HMR.
* Keeping the policy strict required three changes, all verified with zero violations across the full E2E
  journey on the production build:
  * The toast library, which injected `<style>` elements, was replaced.
  * Zod runs in `jitless` mode, so it never probes `new Function`.
  * The scroll-lock stylesheet receives the nonce.
* API: `default-src 'none'; frame-ancestors 'none'; base-uri 'none'` on every response (it serves JSON and
  downloads only).

## Untrusted content & AI safety
* Job descriptions are sanitised with `nh3`: allow-listed tags, `http/https/mailto` links only,
  `rel=noopener`.
* Resumes, job descriptions, emails, user notes and interview answers are passed to models inside
  `<untrusted_document>` blocks:
  * Look-alike closing tags are stripped, so injected text can't escape the block (tested).
  * A fixed system rule says these blocks are data, never instructions and never authorisation.
* The assistant's tools are read-only and user-scoped; no tool submits, sends or changes settings.
* Fabrication guard: AI suggestions that introduce skills or numbers absent from the resume are flagged and
  cannot be accepted without explicit acknowledgement. Unknown application answers stay blank.
* **Cost controls**: per-user rolling 24h budget.
  * Models without a price entry are charged at the highest known rate, so a budget can't be bypassed.
  * Per-user model overrides are limited to the default model plus `JOBPILOT_LLM_ALLOWED_MODELS`.

## Files
* Uploads are streamed and cut off at the size limit (413). Validation checks:
  * extension **and** magic bytes;
  * DOCX structure;
  * macro rejection;
  * a zip-bomb bound (20× the size limit).
* Files are stored outside any web root under random keys; path traversal is rejected. Originals are never
  modified; tailored versions are separate files.

## Secrets & data
* All configuration is environment-driven. Production refuses to start when:
  * the secret key is the default;
  * the encryption key is missing or not a valid Fernet key;
  * cookies aren't marked secure;
  * Redis isn't configured.
* Integration credentials (Gmail OAuth tokens) are Fernet-encrypted. Gmail scopes are `gmail.readonly` +
  `gmail.compose` (drafts only); JobPilot never sends email. Only rule-classified job mail is stored.
* Logs are structured JSON with method, path, status and timing — no bodies, query strings or headers. A
  scan of all V1.1 test-run logs found no passwords, tokens, cookies or keys.
* Backups are `0600` archives with SHA-256 manifests. Database credentials are stripped from manifests.
* Users can export all their data and delete their account; deletion cascades through every table and
  their files.

## MCP server
* Talks to the API only, with a scoped personal token. There is no database access, and approval rules
  apply exactly as for the web app.
* stdio by default. The HTTP transport binds **loopback only** (other hosts are refused) with DNS-rebinding
  protection, because it has no authentication of its own.
* Error messages never echo the token (tested). Job descriptions returned to clients are labelled untrusted.

## V1.1 review findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | AI daily budget could be bypassed: unpriced models recorded no cost, and users could pick any model as an override | **Fixed** — conservative pricing for unknown models + operator allow-list (tests) |
| 2 | Medium | Logout only cleared the cookie; a copied session token stayed valid for up to 7 days | **Fixed** — server-side `jti` revocation (test) |
| 3 | Medium | Uploads were fully buffered in memory before the size check | **Fixed** — streamed with early 413 (test); also set a proxy body limit |
| 4 | Medium | Rate limits were per process (ineffective with several instances) | **Fixed** — Redis limiter, auth fail-closed; verified against real Redis with two limiter instances |
| 5 | Medium | No Content-Security-Policy | **Fixed** — strict nonce CSP, zero violations in E2E |
| 6 | Medium | MCP HTTP transport is unauthenticated; exposing it would hand out the user's token | **Fixed** — loopback-only binding + DNS-rebinding protection (tests) |
| 7 | Low | Account deletion did not require re-authentication | **Fixed** — password required (test) |
| 8 | Low | A malformed encryption key was only detected when first used | **Fixed** — validated at boot in production (test) |
| 9 | Low | Two settings misled users: the in-app toggle did nothing and daily summaries were never generated | **Fixed** — in-app is shown as always-on; the worker now generates summaries (test) |
| 10 | Medium | Per-IP limits can be spoofed if `--forwarded-allow-ips` is too broad | **Open (configuration)** — documented in DEPLOYMENT.md |
| 11 | Medium | Local file storage prevents running several API instances safely | **Open** — single API instance + volume until object storage exists |
| 12 | Low | No MFA, email verification or self-service password reset | **Open** — product backlog |
| 13 | Low | Audit log retention isn't automated | **Open** — define a policy (e.g. 1 year) and add a purge job |
| 14 | Low | API tokens never expire (they are revocable) | **Open** — consider optional expiry |
| 15 | Info | One secret signs sessions and CSRF; rotation signs everyone out | **Accepted** — documented |

Dependency audit (2026-11-26): `pip-audit` on backend and MCP lockfiles and `npm audit` — **0 known
vulnerabilities**. These audits now run in CI.
