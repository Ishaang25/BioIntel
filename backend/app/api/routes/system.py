"""Health, readiness and operational endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select, text

from app import __version__
from app.api.deps import DbSession, rate_limit
from app.core.config import settings
from app.core.enums import STAGE_ORDER, ClaimCategory, EntityType, EvidenceTier, RiskCategory
from app.db.models import Job
from app.jobs import queue
from app.schemas.api import HealthOut, JobOut

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut, summary="Liveness and configuration")
def health(session: DbSession) -> HealthOut:
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # pragma: no cover
        database = "unavailable"

    try:
        depth = queue.queue_depth()
    except Exception:  # pragma: no cover
        depth = {}

    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        environment=settings.environment,
        database=database,
        llm_provider=settings.llm_provider,
        llm_degraded=settings.llm_provider == "stub",
        retrieval_enabled=settings.retrieval_enabled,
        job_mode=settings.job_execution_mode,
        queue=depth,
    )


@router.get("/ready", summary="Readiness probe")
def ready(session: DbSession) -> Response:
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/vocabularies",
    dependencies=[Depends(rate_limit)],
    summary="Enumerate the domain vocabularies used by the API",
)
def vocabularies() -> dict[str, Any]:
    """Lets the frontend build filters without hard-coding server enums."""
    return {
        "claim_categories": [c.value for c in ClaimCategory],
        "entity_types": [e.value for e in EntityType],
        "evidence_tiers": [t.value for t in EvidenceTier],
        "risk_categories": [r.value for r in RiskCategory],
        "pipeline_stages": [s.value for s in STAGE_ORDER],
    }


@router.get(
    "/jobs",
    response_model=list[JobOut],
    dependencies=[Depends(rate_limit)],
    summary="Inspect the job queue",
)
def list_jobs(session: DbSession, limit: int = 50) -> list[JobOut]:
    rows = session.execute(
        select(Job).order_by(Job.created_at.desc()).limit(min(limit, 200))
    ).scalars()
    return [JobOut.model_validate(row) for row in rows]
