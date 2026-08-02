"""Analysis run endpoints: create, monitor, and read every artefact."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, DbSession, PaginationDep, rate_limit
from app.core.config import settings
from app.core.enums import RunStatus
from app.core.errors import NotFound
from app.core.logging import get_logger
from app.db.models import AnalysisRun, Claim, CompanyProfile, Document, EvidenceItem
from app.db.session import session_scope
from app.reporting.renderer import render_html
from app.schemas.api import (
    AssessmentOut,
    ClaimDetailOut,
    ClaimOut,
    CompanyProfileOut,
    CreateRunRequest,
    DocumentOut,
    EntityOut,
    EntityRefOut,
    EvidenceLinkOut,
    EvidenceOut,
    Page,
    QuestionOut,
    ReportOut,
    RiskOut,
    RunDetailOut,
    RunOut,
    StageOut,
)
from app.services import documents as document_service
from app.services import runs as run_service

log = get_logger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])

TERMINAL_STATUSES = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


@router.post(
    "",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit)],
    summary="Start an analysis",
)
def create_run(
    session: DbSession, principal: CurrentPrincipal, payload: CreateRunRequest
) -> RunOut:
    run = run_service.create_run(
        session,
        document_id=payload.document_id,
        requested_by=principal.label,
        force=payload.force,
    )
    return RunOut.model_validate(run)


@router.get(
    "",
    response_model=Page[RunOut],
    dependencies=[Depends(rate_limit)],
    summary="List analyses",
)
def list_runs(
    session: DbSession,
    page: PaginationDep,
    document_id: Annotated[str | None, Query()] = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
) -> Page[RunOut]:
    items = run_service.list_runs(
        session,
        document_id=document_id,
        status=run_status,
        limit=page.limit,
        offset=page.offset,
    )
    query = select(func.count(AnalysisRun.id))
    if document_id:
        query = query.where(AnalysisRun.document_id == document_id)
    if run_status:
        query = query.where(AnalysisRun.status == run_status)

    # A list of run ids is unusable: the reader knows the company, not the
    # identifier. Both labels are resolved here in two statements rather than
    # per row, so the list stays one query's worth of work.
    labels = _run_labels(session, [r.id for r in items])

    out: list[RunOut] = []
    for run in items:
        row = RunOut.model_validate(run)
        filename, company = labels.get(run.id, (None, None))
        row.document_filename = filename
        row.company_name = company
        out.append(row)

    return Page[RunOut](
        items=out,
        total=int(session.execute(query).scalar_one()),
        limit=page.limit,
        offset=page.offset,
    )


def _run_labels(session: DbSession, run_ids: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """Filename and company name for each run, keyed by run id."""
    if not run_ids:
        return {}
    rows = session.execute(
        select(AnalysisRun.id, Document.filename, CompanyProfile.company_name)
        .join(Document, Document.id == AnalysisRun.document_id)
        .outerjoin(CompanyProfile, CompanyProfile.run_id == AnalysisRun.id)
        .where(AnalysisRun.id.in_(run_ids))
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


@router.get(
    "/{run_id}",
    response_model=RunDetailOut,
    dependencies=[Depends(rate_limit)],
    summary="Get analysis status and summary",
)
def get_run(session: DbSession, run_id: str) -> RunDetailOut:
    run = run_service.get_run(session, run_id)
    document = document_service.get_document(session, run.document_id)
    profile = run_service.get_profile(session, run_id)

    detail = RunDetailOut.model_validate(run)
    detail.document = DocumentOut.model_validate(document)
    detail.stages = [StageOut.model_validate(s) for s in run_service.get_stages(session, run_id)]
    detail.counts = run_service.run_summary(session, run_id)
    detail.profile = CompanyProfileOut.model_validate(profile) if profile else None
    detail.degraded = bool((run.metrics or {}).get("degraded"))
    return detail


@router.post(
    "/{run_id}/cancel",
    response_model=RunOut,
    dependencies=[Depends(rate_limit)],
    summary="Cancel a running analysis",
)
def cancel_run(session: DbSession, run_id: str) -> RunOut:
    return RunOut.model_validate(run_service.cancel_run(session, run_id))


@router.get(
    "/{run_id}/events",
    summary="Stream progress as server-sent events",
    response_class=StreamingResponse,
)
async def stream_events(run_id: str) -> StreamingResponse:
    """Push progress updates until the run reaches a terminal state.

    Polling ``GET /runs/{id}`` works too; this exists so the UI can show a
    live progress bar without hammering the API during a multi-minute run.
    """
    with session_scope() as session:
        if session.get(AnalysisRun, run_id) is None:
            raise NotFound(f"Analysis run {run_id} was not found.")

    async def generator():
        last_payload: str | None = None
        deadline = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)

        while dt.datetime.now(dt.UTC) < deadline:
            snapshot = await asyncio.to_thread(_progress_snapshot, run_id)
            if snapshot is None:
                yield _sse("error", {"message": "run disappeared"})
                return

            payload = json.dumps(snapshot, default=str)
            if payload != last_payload:
                yield _sse("progress", snapshot)
                last_payload = payload

            if snapshot["status"] in {s.value for s in TERMINAL_STATUSES}:
                yield _sse("done", snapshot)
                return
            await asyncio.sleep(1.5)

        yield _sse("timeout", {"run_id": run_id})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _progress_snapshot(run_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return None
        stages = run_service.get_stages(session, run_id)
        return {
            "run_id": run.id,
            "status": str(run.status),
            "current_stage": str(run.current_stage) if run.current_stage else None,
            "current_activity": run.current_activity,
            "progress": run.progress,
            "error_code": run.error_code,
            "error_message": run.error_message,
            "stages": [
                {
                    "stage": str(s.stage),
                    "status": str(s.status),
                    "duration_ms": s.duration_ms,
                    "error": s.error_message,
                }
                for s in stages
            ],
            "counts": run_service.run_summary(session, run_id),
        }


# ================================================================ artefacts ===
@router.get(
    "/{run_id}/claims",
    response_model=Page[ClaimOut],
    dependencies=[Depends(rate_limit)],
    summary="List extracted claims with their assessments",
)
def list_claims(
    session: DbSession,
    run_id: str,
    page: PaginationDep,
    category: Annotated[str | None, Query()] = None,
    thesis_critical: Annotated[bool, Query()] = False,
) -> Page[ClaimOut]:
    run_service.get_run(session, run_id)
    claims = run_service.get_claims(
        session,
        run_id,
        category=category,
        thesis_critical_only=thesis_critical,
        limit=page.limit,
        offset=page.offset,
    )
    assessments = run_service.get_assessments(session, run_id)

    total_query = select(func.count(Claim.id)).where(Claim.run_id == run_id)
    if category:
        total_query = total_query.where(Claim.category == category)
    if thesis_critical:
        total_query = total_query.where(Claim.is_thesis_critical.is_(True))

    return Page[ClaimOut](
        items=[_to_claim_out(c, assessments.get(c.id)) for c in claims],
        total=int(session.execute(total_query).scalar_one()),
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{run_id}/claims/{claim_id}",
    response_model=ClaimDetailOut,
    dependencies=[Depends(rate_limit)],
    summary="Get one claim with all its evidence",
)
def get_claim(session: DbSession, run_id: str, claim_id: str) -> ClaimDetailOut:
    run_service.get_run(session, run_id)
    claim = session.get(Claim, claim_id)
    if claim is None or claim.run_id != run_id:
        raise NotFound(f"Claim {claim_id} was not found in run {run_id}.")

    assessments = run_service.get_assessments(session, run_id)
    links = run_service.get_evidence_links(session, run_id, claim_id=claim_id)

    detail = ClaimDetailOut.model_validate(_to_claim_out(claim, assessments.get(claim.id)))
    detail.evidence_links = [
        EvidenceLinkOut(
            id=link.id,
            claim_id=link.claim_id,
            stance=link.stance,
            strength=link.strength,
            relevance=link.relevance,
            similarity=link.similarity,
            rationale=link.rationale,
            supporting_quote=link.supporting_quote,
            quote_verification=link.quote_verification,
            caveats=link.caveats,
            evidence=EvidenceOut.model_validate(link.evidence),
        )
        for link in links
    ]
    return detail


def _to_claim_out(claim: Claim, assessment: Any) -> ClaimOut:
    out = ClaimOut.model_validate(claim)
    out.entities = [EntityRefOut.model_validate(e) for e in claim.entities]
    out.assessment = AssessmentOut.model_validate(assessment) if assessment else None
    return out


@router.get(
    "/{run_id}/entities",
    response_model=list[EntityOut],
    dependencies=[Depends(rate_limit)],
    summary="List extracted scientific entities",
)
def list_entities(
    session: DbSession, run_id: str, entity_type: Annotated[str | None, Query()] = None
) -> list[EntityOut]:
    run_service.get_run(session, run_id)
    return [
        EntityOut.model_validate(e)
        for e in run_service.get_entities(session, run_id, entity_type=entity_type)
    ]


@router.get(
    "/{run_id}/evidence",
    response_model=list[EvidenceOut],
    dependencies=[Depends(rate_limit)],
    summary="List every external record retrieved for this run",
)
def list_evidence(session: DbSession, run_id: str) -> list[EvidenceOut]:
    run_service.get_run(session, run_id)
    rows = session.execute(
        select(EvidenceItem)
        .where(EvidenceItem.id.in_(run_service.evidence_ids_for_run(run_id)))
        .order_by(EvidenceItem.publication_year.desc().nulls_last(), EvidenceItem.id)
    ).scalars()
    return [EvidenceOut.model_validate(r) for r in rows]


@router.get(
    "/{run_id}/questions",
    response_model=list[QuestionOut],
    dependencies=[Depends(rate_limit)],
    summary="List generated diligence questions",
)
def list_questions(session: DbSession, run_id: str) -> list[QuestionOut]:
    run_service.get_run(session, run_id)
    return [QuestionOut.model_validate(q) for q in run_service.get_questions(session, run_id)]


@router.get(
    "/{run_id}/risks",
    response_model=list[RiskOut],
    dependencies=[Depends(rate_limit)],
    summary="List identified scientific risks",
)
def list_risks(session: DbSession, run_id: str) -> list[RiskOut]:
    run_service.get_run(session, run_id)
    return [RiskOut.model_validate(r) for r in run_service.get_risks(session, run_id)]


@router.get(
    "/{run_id}/report",
    response_model=ReportOut,
    dependencies=[Depends(rate_limit)],
    summary="Get the Investment Committee memo",
)
def get_report(session: DbSession, run_id: str) -> ReportOut:
    run_service.get_run(session, run_id)
    return ReportOut.model_validate(run_service.get_report(session, run_id))


@router.get(
    "/{run_id}/report/export",
    dependencies=[Depends(rate_limit)],
    summary="Export the memo as markdown or HTML",
)
def export_report(
    session: DbSession,
    run_id: str,
    format: Annotated[str, Query(pattern="^(markdown|md|html)$")] = "markdown",
) -> Response:
    run = run_service.get_run(session, run_id)
    report = run_service.get_report(session, run_id)
    document = document_service.get_document(session, run.document_id)
    profile = run_service.get_profile(session, run_id)
    company = profile.company_name if profile else None
    slug = (company or document.filename.rsplit(".", 1)[0]).replace(" ", "-").lower()[:60]

    if format in ("markdown", "md"):
        return Response(
            content=report.markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{slug}-diligence.md"'},
        )

    from app.reporting.builder import BuiltReport

    built = BuiltReport(
        title=report.title,
        executive_summary=report.executive_summary,
        sections=report.sections,
        recommendation=report.recommendation,
        limitations=report.limitations,
        citations=report.citations,
        overall_score=report.overall_score,
        overall_band=report.overall_band,
        confidence=report.confidence,
        score_breakdown=report.score_breakdown,
    )
    html = render_html(
        built,
        company_name=company,
        document_name=document.filename,
        generated_at=report.created_at,
        degraded=bool((run.metrics or {}).get("degraded")),
        scorecard=report.scorecard or None,
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{slug}-diligence.html"'},
    )


@router.get(
    "/{run_id}/metrics",
    dependencies=[Depends(rate_limit)],
    summary="Get run telemetry (stages, tokens, cost)",
)
def get_metrics(session: DbSession, run_id: str) -> dict[str, Any]:
    run = run_service.get_run(session, run_id)
    return {
        "run_id": run.id,
        "status": str(run.status),
        "duration_ms": run.duration_ms,
        "config": run.config,
        "metrics": run.metrics,
        "settings": {
            "evidence_per_claim": settings.evidence_per_claim,
            "retrieval_enabled": settings.retrieval_enabled,
        },
    }
