# JobPilot

**Your AI-powered career copilot.** JobPilot discovers roles from company job boards, explains how
well each one fits, tailors your own resume without breaking its formatting, prepares applications
for your approval and tracks them through to an offer. You stay in control: nothing is submitted
or sent without your explicit approval.

```
frontend/     Next.js 16 · React 19 · TypeScript · Tailwind v4 · Radix/shadcn-style UI · React Query
backend/      FastAPI · SQLAlchemy 2 · Alembic · Pydantic · PostgreSQL/SQLite · DB-backed worker
mcp-server/   Official MCP Python SDK — exposes JobPilot to Claude and other MCP clients
```

Read next: [ARCHITECTURE.md](ARCHITECTURE.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) (PRD audit & acceptance)
· [RELEASE_READINESS_V1_1.md](RELEASE_READINESS_V1_1.md) · [DEPLOYMENT.md](DEPLOYMENT.md) · [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md)
· [SECURITY.md](SECURITY.md) · [TESTING.md](TESTING.md) · [CHANGELOG.md](CHANGELOG.md)

## Quick start (local)

Requirements: Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cp .env.example .env               # optional — everything has a safe development default

# API
cd backend
uv sync
uv run alembic upgrade head        # creates backend/data/jobpilot.db (SQLite)
uv run uvicorn app.main:app --port 8000

# Background worker (schedules automations, delivers notifications) — second terminal
cd backend && uv run python -m app.worker.main

# Web app — third terminal
cd frontend
npm install
npm run dev                        # http://localhost:3000
```

Then: create an account → upload a resume (DOCX keeps formatting) → finish onboarding → **Discover
Jobs → Manage sources** and follow a board, e.g. `https://jobs.ashbyhq.com/ramp`,
`https://boards.greenhouse.io/stripe` or `https://jobs.lever.co/palantir`.

### Optional integrations

| Feature | Configure | Without it |
|---|---|---|
| AI assistant, rewording, answer drafts, cover letters, interview feedback | `JOBPILOT_LLM_PROVIDER/_MODEL/_API_KEY` (Anthropic or any OpenAI-compatible endpoint) | Deterministic parsing, matching, reorder-only tailoring and heuristic interview feedback still work; AI screens say so |
| Gmail reply tracking & drafts | Google OAuth client id/secret | Log messages manually |
| WhatsApp notifications | WhatsApp Business Cloud API token + phone number id | In-app notifications only |
| PostgreSQL | `DATABASE_URL` | SQLite |
| Redis (shared rate limiting; required in production) | `JOBPILOT_REDIS_URL` | In-process limiter |

Check what's configured and working with `cd backend && uv run python -m app.ops.verify_integrations` (add
`--live` to call the real providers).

### MCP (Claude Desktop / Claude Code)

1. In JobPilot: **Settings → Security → New token** (pick scopes).
2. Add to your MCP client config (Claude Desktop shown; Claude Code: `claude mcp add`):

```json
{
  "mcpServers": {
    "jobpilot": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/jobpilot/mcp-server", "python", "jobpilot_mcp.py"],
      "env": { "JOBPILOT_API_URL": "http://localhost:8000", "JOBPILOT_API_TOKEN": "jp_…" }
    }
  }
}
```

`--transport streamable-http` serves on `127.0.0.1:8765` only (it has no authentication of its own, so
non-loopback binding is refused).

## Tests

```bash
cd backend && uv run pytest                      # 99 tests (2 need Redis); see TESTING.md for PostgreSQL/Redis mode
cd mcp-server && uv run pytest                   # 7 tests
cd frontend && npm test                          # 21 unit/component tests (Vitest)
cd frontend && npx playwright test               # 16 E2E + axe + CSP tests (needs API + web running)
```

CI: `.github/workflows/ci.yml` (backend on PostgreSQL + Redis, MCP, frontend, dependency audit, E2E).

## Production deployment

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for the full plan (topology, secrets, proxy trust, release and rollback,
backups, go/no-go). Nothing has been deployed.
