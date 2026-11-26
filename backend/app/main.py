from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai import service as ai
from app.api import (
    applications,
    assistant,
    auth,
    automation,
    communications,
    insights,
    interviews,
    jobs,
    profile,
    resume_review,
    resumes,
)
from app.config import get_settings
from app.logging import configure_logging

log = logging.getLogger("jobpilot.api")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(logging.WARNING if s.env == "test" else logging.INFO)
    app = FastAPI(
        title="JobPilot API",
        version="1.0.0",
        docs_url="/api/docs" if s.env != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if s.env != "production" else None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # The API serves JSON and file downloads only — nothing it returns should ever execute or be framed.
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers.setdefault("Cache-Control", "no-store")
        # Structured access log — path only, never query strings or bodies (they can contain personal data).
        log.info(
            "request",
            extra={
                "rid": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": {"message": exc.detail, "status": exc.status_code}},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"].removeprefix("Value error, ")}
            for e in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "message": fields[0]["message"] if fields else "Invalid request.",
                    "status": 422,
                    "fields": fields,
                }
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", extra={"path": request.url.path})
        return JSONResponse({"error": {"message": "Something went wrong on our side.", "status": 500}}, status_code=500)

    api = APIRouter(prefix="/api/v1")
    for module in (
        auth,
        profile,
        resumes,
        resume_review,
        jobs,
        applications,
        assistant,
        interviews,
        communications,
        automation,
        insights,
    ):
        api.include_router(module.router)

    @api.get("/health", tags=["system"])
    def health() -> dict:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @api.get("/health/ready", tags=["system"])
    def ready() -> JSONResponse:
        """Readiness: dependencies reachable. Load balancers should route only to ready instances."""
        from sqlalchemy import text

        from app.db import get_engine
        from app.security.rate_limit import rate_limiter

        checks: dict[str, str] = {}
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:  # noqa: BLE001 - reported, never raised
            checks["database"] = "unavailable"
        checks["rate_limiter"] = f"{rate_limiter.backend}:" + ("ok" if rate_limiter.healthy() else "unavailable")
        # Resume storage must be writable (catches volume ownership/mount problems before the first upload).
        import os

        from app.config import get_settings

        storage = get_settings().storage_dir
        try:
            storage.mkdir(parents=True, exist_ok=True)
            checks["storage"] = "ok" if os.access(storage, os.W_OK) else "not_writable"
        except OSError:
            checks["storage"] = "unavailable"
        ok = all(v.endswith("ok") for v in checks.values())
        return JSONResponse({"status": "ready" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    @api.get("/ai/status", tags=["system"])
    def ai_status() -> dict:
        return ai.status()

    app.include_router(api)
    return app


app = create_app()
