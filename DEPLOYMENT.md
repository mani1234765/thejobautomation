# JobPilot — Production Deployment Plan (V1.1)

> **Status: plan only. Nothing has been deployed.** Deployment waits for explicit owner approval and
> for the decisions in §1.

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

The API must trust forwarded client IPs **only from the proxy hops**, or per-IP rate limits become
spoofable:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 \
  --proxy-headers --forwarded-allow-ips "<proxy and web-tier private IPs/CIDRs>"   # never "*"
```

## 4. Pre-deployment checklist

- [ ] CI green on the release commit (backend on PostgreSQL + Redis, MCP, frontend, E2E/CSP/axe, dependency audit)
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
