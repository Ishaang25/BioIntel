"""Structured logging configuration.

Uses ``structlog`` on top of the stdlib logging module so that third-party
library logs share the same renderer.  Human-readable in local development,
JSON in every deployed environment.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings

_configured = False


def configure_logging(force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.log_json or settings.environment in {"staging", "production"}:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "multipart"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)  # type: ignore[return-value]


def bind_run_context(**kwargs: Any) -> None:
    """Bind identifiers (run_id, document_id...) to all logs on this task."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_run_context() -> None:
    structlog.contextvars.clear_contextvars()
