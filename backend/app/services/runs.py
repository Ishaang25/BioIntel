"""Analysis-run lifecycle service."""

from __future__ import annotations

import asyncio
import datetime as dt
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import STAGE_ORDER, RunStatus, StageStatus
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.db.models import (
    AnalysisRun,
    Claim,
    ClaimAssessment,
    ClaimEvidenceLink,
    CompanyProfile,
    DiligenceQuestion,
    Document,
    Entity,
    EvidenceItem,
    Report,
    RiskFlag,
    RunStage,
)
from app.jobs import queue
from app.services.documents import get_document

log = get_logger(__name__)

ACTIVE_STATUSES = (RunStatus.PENDING, RunStatus.RUNNING)


def create_run(
    session: Session,
    *,
    document_id: str,
    requested_by: str | None = None,
    force: bool = False,
) -> AnalysisRun:
    """Create a run and enqueue it (or execute it inline in dev/test mode)."""
    document = get_document(session, document_id)

    if not force:
        active = (
            session.execute(
                select(AnalysisRun).where(
                    AnalysisRun.document_id == document_id,
                    AnalysisRun.status.in_(ACTIVE_STATUSES),
                )
            )
            .scalars()
            .first()
        )
        # "Already in progress" must mean something is actually in progress.
        # A run whose worker died stays `running` for ever, and refusing on
        # the status alone locks the document out of the product permanently
        # -- restart-proof, because the row is what is wrong. Check that the
        # run is genuinely alive, and reclaim it if it is not.
        if active is not None and _is_abandoned(session, active):
            _abandon(session, active)
            log.warning(
                "run.reclaimed_abandoned",
                run_id=active.id,
                document_id=document_id,
                previous_status=str(active.status),
            )
            active = None
        if active is not None:
            raise Conflict(
                "An analysis of this document is already in progress.",
                detail={"run_id": active.id, "status": active.status},
            )

    run = AnalysisRun(document_id=document.id, requested_by=requested_by)
    session.add(run)
    session.flush()
    run_id = run.id
    session.commit()

    if settings.job_execution_mode == "inline":
        _run_inline(run_id)
    else:
        queue.enqueue(
            queue.JOB_ANALYSE_DOCUMENT,
            {"run_id": run_id, "document_id": document.id},
            run_id=run_id,
        )

    log.info(
        "run.created",
        run_id=run_id,
        document_id=document.id,
        mode=settings.job_execution_mode,
    )
    return session.get(AnalysisRun, run_id)  # type: ignore[return-value]


def _is_abandoned(session: Session, run: AnalysisRun) -> bool:
    """True when nothing alive can advance this run.

    Liveness is decided by the job, not by the run's own status: the run row
    is written *by* the thing that died, so it cannot be trusted to report
    its own death.
    """
    from app.core.enums import JobStatus
    from app.db.models import Job

    if settings.job_execution_mode == "inline":
        future = _INLINE_FUTURES.get(run.id)
        return future is None or future.done()

    jobs = list(session.execute(select(Job).where(Job.run_id == run.id)).scalars())
    if not jobs:
        # No job was ever created for a run the queue was supposed to own.
        return True
    live = [j for j in jobs if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)]
    if not live:
        return True

    # A queued job will be picked up; only a `running` one can be a corpse.
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=settings.job_stale_after_seconds)
    for job in live:
        if job.status is JobStatus.QUEUED:
            return False
        marker = job.heartbeat_at or job.locked_at
        if marker is None or _aware(marker) > cutoff:
            return False
    return True


def _abandon(session: Session, run: AnalysisRun) -> None:
    """Close out a dead run and its jobs so the document is usable again."""
    from app.core.enums import JobStatus
    from app.db.models import Job

    run.status = RunStatus.FAILED
    run.finished_at = dt.datetime.now(dt.UTC)
    run.error_code = run.error_code or "abandoned"
    run.error_message = run.error_message or (
        "This analysis stopped without completing, most likely because the worker was "
        "interrupted. It was closed automatically so a new analysis could start."
    )
    for job in session.execute(select(Job).where(Job.run_id == run.id)).scalars():
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            job.status = JobStatus.FAILED
            job.locked_by = None
            job.last_error = "Superseded: the run was reclaimed after its worker was lost."
    session.flush()


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes; compare them as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


#: Futures for analyses started in ``inline`` mode, so callers can await them.
_INLINE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="biointel-inline")
_INLINE_FUTURES: dict[str, Future[None]] = {}


def _run_inline(run_id: str) -> None:
    """Execute the pipeline in a background thread of the API process.

    Used when ``JOB_EXECUTION_MODE=inline``: a single-user local setup where
    running a separate worker is friction.  The request still returns
    immediately -- an analysis takes minutes -- so the work goes to a thread
    whose future is tracked for :func:`wait_for_inline_runs`.
    """
    from app.pipeline.orchestrator import AnalysisPipeline

    def _execute() -> None:
        asyncio.run(AnalysisPipeline().run(run_id))

    _INLINE_FUTURES[run_id] = _INLINE_EXECUTOR.submit(_execute)


def wait_for_inline_runs(timeout: float = 300.0) -> None:
    """Block until every inline analysis finishes (local scripts and tests)."""
    futures = list(_INLINE_FUTURES.values())
    _INLINE_FUTURES.clear()
    for future in futures:
        try:
            future.result(timeout=timeout)
        except Exception:  # the run itself records its own failure
            log.warning("run.inline_failed", exc_info=True)


def get_run(session: Session, run_id: str) -> AnalysisRun:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise NotFound(f"Analysis run {run_id} was not found.")
    return run


def list_runs(
    session: Session,
    *,
    document_id: str | None = None,
    status: RunStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AnalysisRun]:
    query = select(AnalysisRun).order_by(AnalysisRun.created_at.desc())
    if document_id:
        query = query.where(AnalysisRun.document_id == document_id)
    if status:
        query = query.where(AnalysisRun.status == status)
    return list(session.execute(query.limit(min(limit, 200)).offset(max(0, offset))).scalars())


def cancel_run(session: Session, run_id: str) -> AnalysisRun:
    run = get_run(session, run_id)
    if run.status not in ACTIVE_STATUSES:
        raise Conflict(
            f"Run {run_id} is {run.status} and cannot be cancelled.",
            detail={"status": run.status},
        )
    queue.request_cancel(run_id)
    # A live run stops at its next cancellation checkpoint. A run with no live
    # worker would otherwise stay `running` for ever, so cancelling it has to
    # actually cancel it -- that is the whole point of the button.
    if run.status is RunStatus.PENDING or _is_abandoned(session, run):
        run.status = RunStatus.CANCELLED
        run.finished_at = dt.datetime.now(dt.UTC)
    log.info("run.cancel_requested", run_id=run_id, status=str(run.status))
    return run


def get_stages(session: Session, run_id: str) -> list[RunStage]:
    rows = list(
        session.execute(
            select(RunStage).where(RunStage.run_id == run_id).order_by(RunStage.sequence)
        ).scalars()
    )
    if rows:
        return rows
    # A run that has not started yet still shows its plan in the UI.  These
    # rows are transient, so column defaults do not apply -- set them here.
    return [
        RunStage(
            run_id=run_id,
            stage=stage,
            sequence=index,
            status=StageStatus.PENDING,
            metrics={},
        )
        for index, stage in enumerate(STAGE_ORDER)
    ]


def get_report(session: Session, run_id: str) -> Report:
    report = session.execute(select(Report).where(Report.run_id == run_id)).scalar_one_or_none()
    if report is None:
        raise NotFound("No report exists for this run yet.", detail={"run_id": run_id})
    return report


def get_profile(session: Session, run_id: str) -> CompanyProfile | None:
    return session.execute(
        select(CompanyProfile).where(CompanyProfile.run_id == run_id)
    ).scalar_one_or_none()


def get_claims(
    session: Session,
    run_id: str,
    *,
    category: str | None = None,
    thesis_critical_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[Claim]:
    query = select(Claim).where(Claim.run_id == run_id)
    if category:
        query = query.where(Claim.category == category)
    if thesis_critical_only:
        query = query.where(Claim.is_thesis_critical.is_(True))
    query = query.order_by(
        Claim.is_thesis_critical.desc(), Claim.importance.desc(), Claim.page_number
    )
    return list(session.execute(query.limit(min(limit, 500)).offset(max(0, offset))).scalars())


def get_assessments(session: Session, run_id: str) -> dict[str, ClaimAssessment]:
    rows = session.execute(
        select(ClaimAssessment).where(ClaimAssessment.run_id == run_id)
    ).scalars()
    return {row.claim_id: row for row in rows}


def get_evidence_links(
    session: Session, run_id: str, *, claim_id: str | None = None
) -> list[ClaimEvidenceLink]:
    query = select(ClaimEvidenceLink).where(ClaimEvidenceLink.run_id == run_id)
    if claim_id:
        query = query.where(ClaimEvidenceLink.claim_id == claim_id)
    return list(session.execute(query.order_by(ClaimEvidenceLink.strength.desc())).scalars())


def get_entities(session: Session, run_id: str, *, entity_type: str | None = None) -> list[Entity]:
    query = select(Entity).where(Entity.run_id == run_id)
    if entity_type:
        query = query.where(Entity.entity_type == entity_type)
    return list(session.execute(query.order_by(Entity.salience.desc())).scalars())


def get_questions(session: Session, run_id: str) -> list[DiligenceQuestion]:
    return list(
        session.execute(
            select(DiligenceQuestion)
            .where(DiligenceQuestion.run_id == run_id)
            .order_by(DiligenceQuestion.rank)
        ).scalars()
    )


def get_risks(session: Session, run_id: str) -> list[RiskFlag]:
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    rows = list(session.execute(select(RiskFlag).where(RiskFlag.run_id == run_id)).scalars())
    rows.sort(key=lambda r: severity_rank.get(str(r.severity), 9))
    return rows


def run_summary(session: Session, run_id: str) -> dict[str, Any]:
    """Counts used by the run detail header, in one query pass."""
    counts = {
        "claims": session.execute(
            select(func.count(Claim.id)).where(Claim.run_id == run_id)
        ).scalar_one(),
        "entities": session.execute(
            select(func.count(Entity.id)).where(Entity.run_id == run_id)
        ).scalar_one(),
        "evidence_links": session.execute(
            select(func.count(ClaimEvidenceLink.id)).where(ClaimEvidenceLink.run_id == run_id)
        ).scalar_one(),
        "questions": session.execute(
            select(func.count(DiligenceQuestion.id)).where(DiligenceQuestion.run_id == run_id)
        ).scalar_one(),
        "risks": session.execute(
            select(func.count(RiskFlag.id)).where(RiskFlag.run_id == run_id)
        ).scalar_one(),
    }
    return {k: int(v) for k, v in counts.items()}


def evidence_ids_for_run(run_id: str) -> Select[tuple[str]]:
    """Sub-select of the distinct evidence ids a run linked to.

    De-duplication happens on the id alone. Applying ``DISTINCT`` to the
    evidence rows themselves asks PostgreSQL to compare every selected column,
    and several of them are ``json`` -- a type with no equality operator -- so
    the query fails outright with

        could not identify an equality operator for type json

    Comparing one indexed ``varchar`` is also strictly less work than hashing a
    wide row that carries abstracts and raw API payloads.
    """
    return (
        select(ClaimEvidenceLink.evidence_id).where(ClaimEvidenceLink.run_id == run_id).distinct()
    )


def evidence_for_run(session: Session, run_id: str) -> list[EvidenceItem]:
    return list(
        session.execute(
            select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids_for_run(run_id)))
        ).scalars()
    )


def latest_run_for_document(session: Session, document_id: str) -> AnalysisRun | None:
    return session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.document_id == document_id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def document_for_run(session: Session, run: AnalysisRun) -> Document:
    return get_document(session, run.document_id)
