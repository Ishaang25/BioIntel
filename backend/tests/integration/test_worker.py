"""Worker loop: claiming, success, failure, retry, cancellation, shutdown."""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from app.core.enums import JobStatus, RunStatus
from app.db.models import AnalysisRun, Job
from app.db.session import session_scope
from app.jobs import queue
from app.jobs.worker import Worker
from app.pipeline.orchestrator import CancelledError
from app.services.documents import store_document
from tests.fixtures.sample_deck import neurogen_pdf


@pytest.fixture
def queued_run() -> tuple[str, str]:
    """A document, an analysis run, and a queued job for it."""
    with session_scope() as session:
        document, _ = store_document(session, data=neurogen_pdf(), filename="deck.pdf")
        session.flush()
        run = AnalysisRun(document_id=document.id)
        session.add(run)
        session.flush()
        run_id = run.id
    job_id = queue.enqueue(queue.JOB_ANALYSE_DOCUMENT, {"run_id": run_id}, run_id=run_id)
    return run_id, job_id


def _job_status(job_id: str) -> JobStatus:
    with session_scope() as session:
        return session.get(Job, job_id).status


async def run_worker_until(worker: Worker, condition, *, timeout: float = 30.0) -> None:
    """Run the worker until ``condition()`` holds, then shut it down cleanly.

    Waiting on an explicit condition rather than a drained queue matters for
    the retry test: a requeued job leaves the queue non-empty, and waiting for
    it to drain would sit through the backoff and observe a second attempt.
    """
    task = asyncio.create_task(worker.run())
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
        if await asyncio.to_thread(condition):
            break
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=timeout)


def job_status(job_id: str) -> JobStatus:
    with session_scope() as session:
        job = session.get(Job, job_id)
        return job.status if job else JobStatus.FAILED


async def run_worker_until_idle(worker: Worker, *, timeout: float = 30.0) -> None:
    def drained() -> bool:
        depth = queue.queue_depth()
        return not depth.get("queued") and not depth.get("running")

    await run_worker_until(worker, drained, timeout=timeout)


class FakePipeline:
    """Stands in for AnalysisPipeline so the worker is tested, not the pipeline."""

    calls: list[str] = []
    behaviour: str = "succeed"

    def __init__(self, **kwargs) -> None:
        self.should_cancel = kwargs.get("should_cancel", lambda: False)

    async def run(self, run_id: str):
        FakePipeline.calls.append(run_id)
        await asyncio.sleep(0)
        if FakePipeline.behaviour == "fail":
            raise RuntimeError("pipeline exploded")
        if FakePipeline.behaviour == "cancel":
            raise CancelledError()
        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
            if run is not None:
                run.status = RunStatus.SUCCEEDED
                run.progress = 1.0
        return None


@pytest.fixture(autouse=True)
def fake_pipeline(monkeypatch):
    FakePipeline.calls = []
    FakePipeline.behaviour = "succeed"
    monkeypatch.setattr("app.jobs.worker.AnalysisPipeline", FakePipeline)
    return FakePipeline


@pytest.fixture(autouse=True)
def fast_polling(settings, monkeypatch):
    monkeypatch.setattr(settings, "job_poll_interval_seconds", 0.05)
    monkeypatch.setattr(settings, "job_heartbeat_seconds", 0.05)
    monkeypatch.setattr(settings, "job_stale_after_seconds", 60.0)


class TestWorkerLoop:
    async def test_claims_and_completes_a_job(self, queued_run, fake_pipeline):
        run_id, job_id = queued_run
        await run_worker_until_idle(Worker(concurrency=1))

        assert fake_pipeline.calls == [run_id]
        with session_scope() as session:
            assert session.get(Job, job_id).status is JobStatus.SUCCEEDED
            assert session.get(AnalysisRun, run_id).status is RunStatus.SUCCEEDED

    async def test_pipeline_failure_requeues_the_job(self, queued_run, fake_pipeline):
        _, job_id = queued_run
        fake_pipeline.behaviour = "fail"
        await run_worker_until(
            Worker(concurrency=1),
            lambda: bool(fake_pipeline.calls) and job_status(job_id) is JobStatus.QUEUED,
            timeout=10.0,
        )

        with session_scope() as session:
            job = session.get(Job, job_id)
        # Requeued with backoff rather than abandoned.
        assert job.status is JobStatus.QUEUED
        assert job.attempts == 1
        assert "pipeline exploded" in job.last_error
        assert job.run_after > dt.datetime.now(dt.UTC)

    async def test_cancellation_marks_the_job_cancelled(self, queued_run, fake_pipeline):
        _, job_id = queued_run
        fake_pipeline.behaviour = "cancel"
        await run_worker_until(
            Worker(concurrency=1),
            lambda: job_status(job_id) is JobStatus.CANCELLED,
            timeout=10.0,
        )
        assert job_status(job_id) is JobStatus.CANCELLED

    async def test_processes_several_jobs(self, fake_pipeline):
        run_ids = []
        with session_scope() as session:
            document, _ = store_document(session, data=neurogen_pdf(), filename="deck.pdf")
            session.flush()
            for _ in range(3):
                run = AnalysisRun(document_id=document.id)
                session.add(run)
                session.flush()
                run_ids.append(run.id)
        for run_id in run_ids:
            queue.enqueue(queue.JOB_ANALYSE_DOCUMENT, {"run_id": run_id}, run_id=run_id)

        await run_worker_until_idle(Worker(concurrency=2))
        assert sorted(fake_pipeline.calls) == sorted(run_ids)

    async def test_idle_worker_shuts_down_promptly(self):
        worker = Worker(concurrency=1)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.2)
        worker.request_shutdown()
        await asyncio.wait_for(task, timeout=5.0)

    async def test_job_without_a_run_id_fails_permanently(self, fake_pipeline):
        """A malformed payload cannot be retried into working."""
        job_id = queue.enqueue(queue.JOB_ANALYSE_DOCUMENT, {})
        worker = Worker(concurrency=1)

        await run_worker_until(
            worker,
            lambda: _job_status(job_id) is JobStatus.FAILED,
        )

        assert _job_status(job_id) is JobStatus.FAILED
        # Never handed to the pipeline: it would have failed there with an
        # error that named neither the job nor the real cause.
        assert fake_pipeline.calls == []

    async def test_ignores_other_job_types(self, fake_pipeline):
        queue.enqueue("some_other_type", {"run_id": "run_x"})
        worker = Worker(concurrency=1)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.4)
        worker.request_shutdown()
        await asyncio.wait_for(task, timeout=5.0)

        assert fake_pipeline.calls == []
        assert queue.queue_depth().get("queued") == 1
