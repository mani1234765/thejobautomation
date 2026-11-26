# JobPilot API and worker image (same image; the worker overrides the command).
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# PostgreSQL 17 client tools so `python -m app.ops.backup` can run pg_dump/pg_restore in the container.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends curl ca-certificates gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && . /etc/os-release \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update -qq && apt-get install -y -qq --no-install-recommends postgresql-client-17 \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .

RUN useradd --create-home --uid 10001 jobpilot && mkdir -p /app/storage && chown -R jobpilot /app/storage
USER jobpilot

ENV JOBPILOT_STORAGE_DIR=/app/storage PORT=10000
EXPOSE 10000
# --no-proxy-headers: client IPs come from JOBPILOT_TRUSTED_PROXY_HOPS (see app/api/deps.py), never from
# uvicorn's left-most X-Forwarded-For handling.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --no-proxy-headers"]
