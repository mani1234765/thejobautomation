# JobPilot — Deployment Plan (V1.2: staging on Render)

> **Status: plan and configuration only. Nothing has been deployed.** Applying `render.yaml` creates
> billable resources and waits for the owner's explicit approval.

## 1. Decisions needed from the owner before deploying

| Decision | Options | Recommendation |
|---|---|---|
| Hosting | Container platform (Fly.io, Render, Railway, ECS/Fargate, Cloud Run) or a VM | A container platform with managed PostgreSQL and Redis |
| Domain | e.g. `app.<your-domain>` | Single origin for the web app; the API stays internal |
| File storage | Persistent volume (single API instance) or shared storage | Start with **one API instance + persistent volume** (see §7); add S3-compatible storage before scaling the API out |
| Credentials | AI provider key, Google OAuth client, WhatsApp Cloud API | Configure in a secrets manager; verify with `verify_integrations --live` (§6) |

## 2. Topology

```
Internet ─TLS─▶ Reverse proxy / load balancer (HTTPS, HSTS, body limit 10 MB)
                    │
                    ▼
            Next.js web (≥2 instances, stateless)  ── /api/* rewrite ──▶  FastAPI API (internal)
                                                                            │      │
                                       Worker (1 instance; ≥2 allowed on PG)│      │
                                                   │                       ▼      ▼
                                                   └────────────▶  PostgreSQL 17   Redis 7
                                                                   (managed, PITR) (managed)
                                                   Private file volume (resumes)
```

* Only the proxy is public. The API, worker, database and Redis are on a private network.
* The MCP server is **not** deployed centrally. Users run it locally against the public URL with
  their own token (it refuses non-loopback HTTP binding by design).

## 3. Configuration (secrets manager → environment)

Required (the app refuses to boot in production without the starred items):

| Variable | Notes |
|---|---|
| `JOBPILOT_ENV=production` ★ | Enables production guards; disables OpenAPI docs |
| `DATABASE_URL` ★ | `postgresql+psycopg://…` with TLS (`?sslmode=require`) |
| `JOBPILOT_SECRET_KEY` ★ | ≥ 32 random bytes; rotating it signs everyone out |
| `JOBPILOT_ENCRYPTION_KEY` ★ | Fernet key; **back it up separately** — without it, stored Gmail tokens are unrecoverable |
| `JOBPILOT_COOKIE_SECURE=true` ★ | HTTPS-only cookies |
| `JOBPILOT_REDIS_URL` ★ | Shared rate limiting (`rediss://` with TLS where available) |
| `JOBPILOT_PUBLIC_APP_URL` | `https://app.<domain>` — also the OAuth redirect base |
| `JOBPILOT_STORAGE_DIR` | Mount point of the private file volume |
| `JOBPILOT_API_ORIGIN` | Web → API internal URL (web service only) |
| AI / Gmail / WhatsApp variables | Optional; see `.env.example` |

Client IPs (for rate limits and audit) come from `JOBPILOT_TRUSTED_PROXY_HOPS`, the number of proxies in
front of the API that **append** to `X-Forwarded-For`. The real client is the right-most of those entries,
and anything further left (client-supplied) is ignored. uvicorn runs with `--no-proxy-headers`. Its own
`--forwarded-allow-ips '*'` would trust the **left-most**, spoofable entry (verified in uvicorn's source).

* Measured: the Next.js `/api` rewrite passes `X-Forwarded-For` through **unchanged** and adds nothing.
* Behind one platform load balancer → `JOBPILOT_TRUSTED_PROXY_HOPS=1`. This is safe only if the API
  is **not** reachable except through that path (a private service).
* **Staging acceptance must confirm it**: sign in from a known IP and check the `auth.login` audit
  event's `ip`. If the platform adds a second appending hop (e.g. a CDN), raise the value to 2.

## 4. Pre-deployment checklist

- [x] CI green on the release commit (GitHub run 45883858343, `bce77be`) (backend on PostgreSQL + Redis, MCP, frontend, E2E/CSP/axe, dependency audit)
- [ ] Secrets created in the secrets manager; none in images, repo or CI logs
- [ ] Managed PostgreSQL with automated backups + PITR (≥ 7 days), TLS enforced
- [ ] Managed Redis reachable only from the private network
- [ ] Private, encrypted volume for `JOBPILOT_STORAGE_DIR`, included in backups (§8)
- [ ] Proxy: TLS 1.2+, HSTS, request body limit ≈ 10 MB, timeouts ≥ 120 s for AI endpoints
- [ ] Google OAuth redirect URI registered exactly as `https://app.<domain>/api/v1/integrations/gmail/callback`
- [ ] Alerting on 5xx rate, `/api/v1/health/ready` failures, failed automation runs, disk usage

## 5. Release procedure

1. **Backup**: `python -m app.ops.backup create --out <backup-dir>`, then `verify` it (§8).
2. **Migrate**: `alembic upgrade head` (one-off job, before new code serves traffic). All V1.1 migrations are
   additive and reversible.
3. **Deploy** the API, then the worker, then the web tier (rolling; readiness gate `/api/v1/health/ready`).
4. **Smoke test** (≈ 5 min): sign up → upload resume → follow a board → open a job → prepare →
   request approval → approve → confirm submission; check `Content-Security-Policy` on `/login`.
5. **Verify integrations**: `python -m app.ops.verify_integrations --live` on the API host (AI checks cost a
   few cents; WhatsApp send and Gmail draft run only with their explicit flags).
6. **Watch** error rates and logs for 30 minutes.

## 6. Rollback

* **Code**: redeploy the previous image (stateless tiers).
* **Schema**: `alembic downgrade -1` per migration (V1.1 migrations: drop `revoked_sessions`, drop
  `ai_usage.cost_estimated`); both were exercised in the migration round-trip tests.
* **Data**: restore with `python -m app.ops.backup restore --archive … --target-url <new DB>`, then point
  `DATABASE_URL` at it (restore into a new database rather than over the live one).

## 7. Scaling constraints

* **Files**: `FileStore` writes to a local path. Running more than one API instance requires a shared volume
  (NFS/EFS) or an object-storage implementation — **not built yet**.
* **Rate limiting** is shared through Redis (multi-instance safe).
* **Worker**: on PostgreSQL, multiple workers are safe (`SKIP LOCKED`, idempotency keys); one is enough at V1 load.
* **Pages are dynamically rendered** (nonce CSP); size the web tier for SSR per request, and don't CDN-cache HTML.

## 8. Backup & recovery

| What | How | Frequency / retention |
|---|---|---|
| Database | Managed PITR + `app.ops.backup create` (pg_dump -Fc) | PITR continuous, 7–35 days; logical daily, 30 days |
| Resume files | Included in `app.ops.backup` archives; volume snapshots | Daily, 30 days |
| Encryption key | Secrets manager + offline copy, separate from data backups | On creation / rotation |

Archives carry SHA-256 checksums (`verify`), are written `0600`, and must be stored encrypted off-host.
**Restore drill**: monthly, restore the latest archive into a scratch database and run the smoke test. The
automated drill (`tests/test_backup.py`) does this on every CI run against PostgreSQL.

## 9. Go / no-go

Go when: CI green · checklist complete · smoke test passes · `verify_integrations` shows no FAIL (BLOCKED
items are acceptable only if the related feature is intentionally left unconfigured and the UI shows
"Requires setup").


## 10. Staging on Render (V1.2)

### Why Render
| Need | Render | Fly.io | Railway |
|---|---|---|---|
| Public Next.js + **private** API (not internet-reachable) | Web service + private service (`pserv`) | Possible via 6PN private networking, more manual | Private networking, project-scoped |
| Background worker | `worker` service type | Machines / process groups | Worker services |
| Managed Postgres | Yes | Managed Postgres (separate product, priced per plan + storage) | Yes, in-project |
| Redis-compatible store | Key Value (Valkey) | Upstash via CLI | Yes, in-project |
| Persistent resume storage | Disk on one service (single instance, brief downtime per deploy) | Volume attached to one Machine | Volumes |
| Infrastructure as code | `render.yaml` Blueprint, CLI validation | `fly.toml` per app | Config-as-code, less complete |
| Already on this machine | Render CLI v2.35.0 | — | — |

Render is recommended: every JobPilot tier maps onto a native service type, the API can be truly private
(which is what makes trusting one forwarded hop safe), and the whole stack is one reviewed Blueprint.
Fly.io gives more control but more operations work; Railway is simplest but its private networking and
IaC are less explicit. Comparison sources: Render Blueprint spec and disk docs; Fly/Railway comparison
articles (largely vendor-authored, so treat the claims cautiously).

### What `render.yaml` defines (unapplied)
* `jobpilot-db` Postgres 17, private only (`ipAllowList: []`).
* `jobpilot-kv` Key Value, `noeviction` (rate-limit windows are never evicted), private only.
* `jobpilot-api` **private service** (Docker, `backend/Dockerfile`):
  * `preDeployCommand: alembic upgrade head`;
  * a 5 GB disk at `/app/storage`;
  * uvicorn with `--no-proxy-headers`, `JOBPILOT_TRUSTED_PROXY_HOPS=1`.
* `jobpilot-worker` (same image, `python -m app.worker.main`).
* `jobpilot-web` (Node 22, `npm ci && npm run build`, health check `/login`); reaches the API via its
  private `host` + port 10000.
* Shared env group:
  * Set by the Blueprint: `JOBPILOT_ENV=production` (staging runs with production guards), secure cookies,
    and a generated secret key.
  * Prompted in the Dashboard (`sync: false`): the Fernet key, public URL, and every integration credential.

### Constraints accepted for staging
* **Disk:** the API is limited to **one instance**, and each deploy has a few seconds of downtime, because
  Render stops the old instance before starting the new one. The disk can't be read during build or
  pre-deploy.
* **Image:** the Dockerfile has **not been built locally** (no Docker on the development machine); Render's
  first build is its first build.
* **Disk ownership:** the container runs as non-root (uid 10001). If the mounted disk isn't writable,
  `/api/v1/health/ready` reports `storage: not_writable` before any upload is attempted.

### Procedure (requires approval at each ⚑)
1. ⚑ `render login` (owner), then `render blueprints validate render.yaml` (read-only). Done 2026-11-27: only
   `need_payment_info` — add a payment method first.
2. ⚑ Push the repository to GitHub (CI must be green). Done: green.
3. ⚑ Create the Blueprint from the repo in the Render Dashboard. Enter the `sync: false` values: generate the
   Fernet key locally; set `JOBPILOT_PUBLIC_APP_URL` to the web service URL.
4. Wait for `/api/v1/health/ready` (via the web URL) to report `ready`; run the staging acceptance checklist
   (RELEASE_READINESS_V1_2.md §Staging acceptance).
5. Google OAuth: add `https://<web host>/api/v1/integrations/gmail/callback` as an authorised redirect URI.
6. Run `python -m app.ops.verify_integrations --live` from a Render shell on `jobpilot-api`.
7. Backups: confirm the Postgres plan's backup/PITR retention in the Dashboard; run
   `python -m app.ops.backup create --out /app/storage/backups` and then a restore into a scratch database.

### MCP in staging
Not deployed. The MCP server runs on each user's machine over stdio (or loopback HTTP) with a scoped token
against the staging URL. No unauthenticated MCP endpoint is exposed.
