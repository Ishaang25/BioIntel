"""Durable job queue backed by the primary database.

Why not Celery/RQ: the workload is a handful of long-running analyses, not
millions of small tasks.  A single ``jobs`` table with optimistic locking gives
at-least-once delivery, retries with backoff, crash recovery and cancellation
without adding a broker to the deployment.  It also means a job and the rows it
writes share one transactional store, so there is no window where a job is
"done" but its results are not visible.

Claiming is safe under concurrency on both backends: the ``UPDATE ... WHERE
status = 'queued' AND id = ?`` guard means two workers racing for the same row
produce exactly one winner.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from typing import Any

from sqlalchemy import and_, or_, select, update

from app.core.config import settings
from app.core.enums import JobStatus
from app.core.logging import get_logger
from app.db.models import Job
from app.db.session import session_scope

log = get_logger(__name__)

JOB_ANALYSE_DOCUMENT = "analyse_document"


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def enqueue(
    job_type: str,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
    priority: int = 100,
    delay_seconds: float = 0.0,
    max_attempts: int | None = None,
) -> str:
    run_after = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=delay_seconds)
    with session_scope() as session:
        job = Job(
            job_type=job_type,
            payload=payload,
            run_id=run_id,
            priority=priority,
            run_after=run_after,
            max_attempts=max_attempts or settings.job_max_attempts,
        )
        session.add(job)
        session.flush()
        job_id = job.id
    log.info("job.enqueued", job_id=job_id, job_type=job_type, run_id=run_id)
    return job_id


def claim_next(worker: str, *, job_types: list[str] | None = None) -> dict[str, Any] | None:
    """Atomically claim the highest-priority due job.

    Returns a plain dict (not an ORM object) so the caller can work without
    holding a session for the duration of the job.
    """
    now = dt.datetime.now(dt.UTC)
    with session_scope() as session:
        query = (
            select(Job)
            .where(Job.status == JobStatus.QUEUED, Job.run_after <= now)
            .order_by(Job.priority.asc(), Job.run_after.asc())
            .limit(10)
        )
        if job_types:
            query = query.where(Job.job_type.in_(job_types))

        for candidate in session.execute(query).scalars():
            result = session.execute(
                update(Job)
                .where(Job.id == candidate.id, Job.status == JobStatus.QUEUED)
                .values(
                    status=JobStatus.RUNNING,
                    locked_by=worker,
                    locked_at=now,
                    heartbeat_at=now,
                    attempts=Job.attempts + 1,
                    updated_at=now,
                )
            )
            if result.rowcount:
                session.flush()
                session.refresh(candidate)
                return {
                    "id": candidate.id,
                    "job_type": candidate.job_type,
                    "payload": dict(candidate.payload or {}),
                    "run_id": candidate.run_id,
                    "attempts": candidate.attempts,
                    "max_attempts": candidate.max_attempts,
                }
    return None


def heartbeat(job_id: str) -> bool:
    """Refresh a job's liveness marker; returns False if cancellation was requested."""
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return False
        job.heartbeat_at = dt.datetime.now(dt.UTC)
        return not job.cancel_requested


def is_cancelled(job_id: str) -> bool:
    with session_scope() as session:
        job = session.get(Job, job_id)
        return bool(job and job.cancel_requested)


def request_cancel(run_id: str) -> int:
    """Ask any job for ``run_id`` to stop; returns the number of jobs signalled."""
    with session_scope() as session:
        result = session.execute(
            update(Job)
            .where(
                Job.run_id == run_id,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .values(cancel_requested=True, updated_at=dt.datetime.now(dt.UTC))
        )
        return int(result.rowcount or 0)


def mark_succeeded(job_id: str) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.SUCCEEDED
        job.locked_by = None
        job.last_error = None


def mark_failed(job_id: str, error: str, *, retry: bool = True) -> bool:
    """Fail a job, scheduling a retry when attempts remain.

    Returns True when the job was re-queued.
    """
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return False
        job.last_error = error[:4000]
        job.locked_by = None
        if retry and job.attempts < job.max_attempts:
            backoff = min(300.0, 15.0 * (2 ** (job.attempts - 1)))
            job.status = JobStatus.QUEUED
            job.run_after = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=backoff)
            _requeue_run(session, job.run_id)
            log.warning(
                "job.retry_scheduled",
                job_id=job_id,
                attempt=job.attempts,
                backoff_seconds=backoff,
            )
            return True
        job.status = JobStatus.FAILED
        # The pipeline normally records its own failure; this covers the case
        # where it never got the chance (crash before or outside the run).
        _fail_run(session, job.run_id, code="job_failed", message=error[:1000])
        return False


def mark_cancelled(job_id: str) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.CANCELLED
        job.locked_by = None


def release(job_id: str, reason: str) -> None:
    """Hand a claimed job back to the queue without consuming an attempt.

    Used when a worker shuts down with work in flight: the job was never
    given a fair chance, so it should be picked up immediately by the next
    worker rather than waiting out the stale-job timeout.
    """
    now = dt.datetime.now(dt.UTC)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or job.status is not JobStatus.RUNNING:
            return
        job.status = JobStatus.QUEUED
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        job.run_after = now
        job.updated_at = now
        job.last_error = reason[:4000]
        # Not the job's fault: give the attempt back.
        job.attempts = max(0, job.attempts - 1)
        _requeue_run(session, job.run_id)
    log.info("job.released", job_id=job_id, reason=reason[:120])


def reap_stale_jobs() -> int:
    """Re-queue jobs whose worker died mid-run, and move their runs with them.

    A worker refreshes ``heartbeat_at`` while it works; if that stops for
    longer than ``job_stale_after_seconds`` the job is assumed abandoned.

    The run must be transitioned in the same transaction as the job.  Reaping
    the job alone leaves ``AnalysisRun.status = running`` with nothing alive to
    advance it, and because :func:`app.services.runs.create_run` refuses to
    start a second analysis while one is active, that combination locks the
    document out permanently -- surviving restarts, because the row survives
    restarts. The job and the run are one unit of recovery or they are not
    recovery at all.
    """
    now = dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(seconds=settings.job_stale_after_seconds)
    with session_scope() as session:
        stale = list(
            session.execute(
                select(Job).where(
                    Job.status == JobStatus.RUNNING,
                    or_(
                        and_(Job.heartbeat_at.is_not(None), Job.heartbeat_at < cutoff),
                        and_(Job.heartbeat_at.is_(None), Job.locked_at < cutoff),
                    ),
                )
            ).scalars()
        )
        requeued = 0
        abandoned = 0
        for job in stale:
            job.last_error = "Worker stopped responding; job was re-queued."
            job.locked_by = None
            job.updated_at = now
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.FAILED
                abandoned += 1
                _fail_run(
                    session,
                    job.run_id,
                    code="worker_lost",
                    message=(
                        "The worker running this analysis stopped responding and the job "
                        "exhausted its retries. Start a new analysis to try again."
                    ),
                )
            else:
                job.status = JobStatus.QUEUED
                job.run_after = now
                requeued += 1
                _requeue_run(session, job.run_id)
        if stale:
            log.warning(
                "job.reaped_stale", count=len(stale), requeued=requeued, abandoned=abandoned
            )
        return len(stale)


def recover_orphaned_runs() -> int:
    """Fail runs that are active but have no job that could ever finish them.

    The safety net behind :func:`reap_stale_jobs`. A run is orphaned when its
    job is already terminal (failed, cancelled, or gone) while the run itself
    still says pending/running -- which happens if a process dies between
    marking the job and marking the run, or if a job row is lost. Without this
    the document stays locked and the only remedy is editing the database by
    hand, which is not a remedy.
    """
    from app.core.enums import RunStatus
    from app.db.models import AnalysisRun

    terminal = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
    recovered = 0
    with session_scope() as session:
        active_runs = list(
            session.execute(
                select(AnalysisRun).where(
                    AnalysisRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING])
                )
            ).scalars()
        )
        for run in active_runs:
            jobs = list(session.execute(select(Job).where(Job.run_id == run.id)).scalars())
            if jobs and any(job.status not in terminal for job in jobs):
                continue  # a job is still queued or running; leave it alone
            if not jobs and run.status is RunStatus.PENDING:
                # Enqueued inline, or the job was never created. Give the
                # caller the benefit of the doubt rather than killing a run
                # that has not had a chance to start.
                continue
            run.status = RunStatus.FAILED
            run.finished_at = dt.datetime.now(dt.UTC)
            run.error_code = run.error_code or "orphaned"
            run.error_message = run.error_message or (
                "This analysis stopped without completing, most likely because the worker "
                "was interrupted. Start a new analysis to try again."
            )
            recovered += 1
    if recovered:
        log.warning("run.orphans_recovered", count=recovered)
    return recovered


def _fail_run(session: Any, run_id: str | None, *, code: str, message: str) -> None:
    from app.core.enums import RunStatus
    from app.db.models import AnalysisRun

    if not run_id:
        return
    run = session.get(AnalysisRun, run_id)
    if run is None or run.status not in (RunStatus.PENDING, RunStatus.RUNNING):
        return
    run.status = RunStatus.FAILED
    run.finished_at = dt.datetime.now(dt.UTC)
    run.error_code = code
    run.error_message = message
    log.warning("run.failed_by_reaper", run_id=run_id, code=code)


def _requeue_run(session: Any, run_id: str | None) -> None:
    """Put a re-queued job's run back to pending so progress is not a lie."""
    from app.core.enums import RunStatus
    from app.db.models import AnalysisRun

    if not run_id:
        return
    run = session.get(AnalysisRun, run_id)
    if run is None or run.status is not RunStatus.RUNNING:
        return
    run.status = RunStatus.PENDING
    run.progress = 0.0
    run.current_stage = None
    log.info("run.requeued_by_reaper", run_id=run_id)


def queue_depth() -> dict[str, int]:
    from sqlalchemy import func

    with session_scope() as session:
        rows = session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    return {str(status): int(count) for status, count in rows}
