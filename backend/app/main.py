"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routes import documents, runs, system
from app.core.config import settings
from app.core.errors import BioIntelError
from app.core.logging import configure_logging, get_logger
from app.db.session import create_all, get_engine

log = get_logger(__name__)

DESCRIPTION = """
**BioIntel** performs scientific due diligence on biotech pitch decks.

Upload a PDF and the platform will parse it (including scanned pages, charts and
tables), extract the scientific claims with verifiable provenance, retrieve
relevant published evidence from PubMed, Europe PMC and ClinicalTrials.gov,
adjudicate each claim against that evidence, score its credibility, and produce
a cited Investment Committee memo.

Every claim is traceable to a page of the source document, and every external
statement to a real retrieved record.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings.ensure_directories()

    if settings.environment in {"local", "test"}:
        # Deployed environments run Alembic explicitly; local dev should just work.
        create_all()

    log.info(
        "app.startup",
        version=__version__,
        environment=settings.environment,
        llm_provider=settings.llm_provider,
        job_mode=settings.job_execution_mode,
        database=settings.database_url.split("://", 1)[0],
        auth_enabled=settings.auth_enabled,
    )
    if settings.llm_provider == "stub":
        log.warning(
            "app.llm_degraded",
            message=(
                "No OPENAI_API_KEY configured. BioIntel will run with its deterministic "
                "offline analyser; reports will be clearly marked as degraded."
            ),
        )
    if not settings.auth_enabled and settings.environment != "test":
        log.warning(
            "app.auth_disabled",
            message="No API_KEYS configured; the API is unauthenticated.",
        )

    yield

    get_engine().dispose()
    log.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="BioIntel API",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "BioIntel Engineering"},
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    _register_middleware(app)
    _register_exception_handlers(app)

    app.include_router(system.router, prefix=settings.api_prefix)
    app.include_router(documents.router, prefix=settings.api_prefix)
    app.include_router(runs.router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "name": "BioIntel",
            "version": __version__,
            "docs": "/docs",
            "health": f"{settings.api_prefix}/health",
        }

    return app


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.exception(
                "http.unhandled",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        # Streaming responses (SSE) must not be buffered by intermediaries.
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            response.headers.setdefault("Cache-Control", "no-cache")

        quiet = {"/", f"{settings.api_prefix}/health", f"{settings.api_prefix}/ready"}
        if request.url.path not in quiet:
            log.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        structlog.contextvars.clear_contextvars()
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if settings.environment in {"staging", "production"}:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BioIntelError)
    async def handle_domain_error(request: Request, exc: BioIntelError) -> JSONResponse:
        payload = exc.to_payload()
        payload["request_id"] = request.headers.get("X-Request-ID")
        if exc.status_code >= 500:
            log.error("api.error", code=exc.code, message=exc.message, path=request.url.path)
        else:
            log.info("api.client_error", code=exc.code, path=request.url.path)
        headers = {}
        if exc.code == "rate_limited":
            headers["Retry-After"] = str(exc.detail.get("retry_after_seconds", 60))
        return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_failed",
                "message": "The request payload failed validation.",
                "detail": {"errors": _safe_errors(exc)},
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": _code_for(exc.status_code), "message": str(exc.detail)},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                # Never leak internals to a client; the log has the detail.
                "message": "An unexpected error occurred. The incident has been logged.",
            },
        )


def _safe_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    return [
        {"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg", "")}
        for e in exc.errors()[:20]
    ]


def _code_for(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "document_too_large",
        415: "unsupported_document",
        422: "validation_failed",
        429: "rate_limited",
    }.get(status_code, "error")


app = create_app()
