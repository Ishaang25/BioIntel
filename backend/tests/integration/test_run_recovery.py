"""A crashed analysis must never lock a document out of the product.

The incident: the BioNTech deck reached

    run   status=running   current_stage=claims   finished_at=NULL
    job   status=running   attempts=3/3   last_error="Worker stopped responding"

and every subsequent upload attempt returned *"An analysis of this document is
already in progress."* -- across service restarts, because the row is what was
wrong. ``reap_stale_jobs`` re-queued the **job** and never touched the **run**,
while ``create_run`` refuses on the run's status alone. Nothing in the system
could ever move that run out of ``running``, so the only remedy was editing
SQLite by hand.

Each test below is one way a run can die. All of them must end with the user
able to start a new analysis.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.enums import JobStatus, RunStatus
from app.core.errors import Conflict
from app.db.models import AnalysisRun, Job
from app.db.session import session_scope
from app.jobs import queue
from app.services.documents import store_document
from app.services.runs import create_run
from tests.fixtures.sample_deck import neurogen_pdf


@pytest.fixture
def document_id() -> str:
    with session_scope() as session:
        document, _ = store_document(session, data=neurogen_pdf(), filename="deck.pdf")
        session.flush()
        return document.id


def _start(document_id: str) -> str:
    """Create a run the way the API does, without executing it."""
    with session_scope() as session:
        run = AnalysisRun(document_id=document_id)
        session.add(run)
        session.flush()
        run_id = run.id
    queue.enqueue(
        queue.JOB_ANALYSE_DOCUMENT,
        {"run_id": run_id, "document_id": document_id},
        run_id=run_id,
    )
    return run_id


def _claim(run_id: str) -> str:
    job = queue.claim_next("worker-1", job_types=[queue.JOB_ANALYSE_DOCUMENT])
    assert job is not None and job["run_id"] == run_id
    with session_scope() as session:
        session.get(AnalysisRun, run_id).status = RunStatus.RUNNING
    return job["id"]


def _age(job_id: str, seconds: float) -> None:
    """Backdate a job's liveness markers to simulate a worker that stopped."""
    stale = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=seconds)
    with session_scope() as session:
        job = session.get(Job, job_id)
        job.heartbeat_at = stale
        job.locked_at = stale


def _status(run_id: str) -> RunStatus:
    with session_scope() as session:
        return session.get(AnalysisRun, run_id).status


def _rerun(document_id: str) -> str:
    with session_scope() as session:
        return create_run(session, document_id=document_id).id


class TestInterruptedWorker:
    """The worker is killed mid-analysis; the job still has attempts left."""

    def test_reaping_returns_the_run_to_pending(self, document_id, settings, monkeypatch):
        monkeypatch.setattr(settings, "job_stale_after_seconds", 60.0)
        run_id = _start(document_id)
        job_id = _claim(run_id)
        _age(job_id, 300)

        assert queue.reap_stale_jobs() == 1

        # The job goes back on the queue, and the run goes with it. A run left
        # at `running` with a queued job reports progress that nothing is making.
        with session_scope() as session:
            assert session.get(Job, job_id).status is JobStatus.QUEUED
        assert _status(run_id) is RunStatus.PENDING

    def test_a_released_job_is_reclaimable_immediately(self, document_id):
        run_id = _start(document_id)
        job_id = _claim(run_id)

        queue.release(job_id, "Worker shut down while this job was running.")

        assert _status(run_id) is RunStatus.PENDING
        reclaimed = queue.claim_next("worker-2", job_types=[queue.JOB_ANALYSE_DOCUMENT])
        assert reclaimed is not None
        assert reclaimed["id"] == job_id
        # Shutting a worker down is not the job's fault; it keeps its attempts.
        assert reclaimed["attempts"] == 1


class TestExhaustedRetries:
    """The BioNTech case exactly: attempts spent, worker gone, run stranded."""

    def test_run_is_failed_not_left_running(self, document_id, settings, monkeypatch):
        monkeypatch.setattr(settings, "job_stale_after_seconds", 60.0)
        monkeypatch.setattr(settings, "job_max_attempts", 1)
        run_id = _start(document_id)
        job_id = _claim(run_id)
        _age(job_id, 300)

        queue.reap_stale_jobs()

        with session_scope() as session:
            assert session.get(Job, job_id).status is JobStatus.FAILED
        assert _status(run_id) is RunStatus.FAILED

    def test_the_document_can_be_analysed_again(self, document_id, settings, monkeypatch):
        monkeypatch.setattr(settings, "job_stale_after_seconds", 60.0)
        monkeypatch.setattr(settings, "job_max_attempts", 1)
        run_id = _start(document_id)
        _age(_claim(run_id), 300)
        queue.reap_stale_jobs()

        second = _rerun(document_id)
        assert second != run_id


class TestNoWorkerEverRuns:
    """Recovery cannot depend on a worker, because the worker is what died."""

    def test_create_run_reclaims_a_run_whose_worker_is_gone(
        self, document_id, settings, monkeypatch
    ):
        monkeypatch.setattr(settings, "job_stale_after_seconds", 60.0)
        run_id = _start(document_id)
        _age(_claim(run_id), 300)

        # No reaper has run. This is the state the user actually hit after
        # restarting services, and it must not be a dead end.
        second = _rerun(document_id)

        assert second != run_id
        assert _status(run_id) is RunStatus.FAILED
        with session_scope() as session:
            assert session.get(AnalysisRun, run_id).error_code == "abandoned"

    def test_the_superseded_job_is_closed_out(self, document_id, settings, monkeypatch):
        monkeypatch.setattr(settings, "job_stale_after_seconds", 60.0)
        run_id = _start(document_id)
        job_id = _claim(run_id)
        _age(job_id, 300)

        _rerun(document_id)

        with session_scope() as session:
            # Otherwise a worker could still pick up the old job and start a
            # second analysis of the same document behind the new one.
            assert session.get(Job, job_id).status is JobStatus.FAILED

    def test_orphan_recovery_closes_a_run_whose_job_is_terminal(self, document_id):
        run_id = _start(document_id)
        job_id = _claim(run_id)
        with session_scope() as session:
            session.get(Job, job_id).status = JobStatus.FAILED

        assert queue.recover_orphaned_runs() == 1
        assert _status(run_id) is RunStatus.FAILED

    def test_orphan_recovery_leaves_healthy_runs_alone(self, document_id):
        run_id = _start(document_id)
        _claim(run_id)

        assert queue.recover_orphaned_runs() == 0
        assert _status(run_id) is RunStatus.RUNNING


class TestLiveRunsAreStillProtected:
    """Recovery must not become a licence to run the same document twice."""

    def test_a_running_analysis_still_blocks_a_second_one(self, document_id):
        run_id = _start(document_id)
        _claim(run_id)

        with pytest.raises(Conflict):
            _rerun(document_id)

    def test_a_queued_analysis_still_blocks_a_second_one(self, document_id):
        _start(document_id)

        with pytest.raises(Conflict):
            _rerun(document_id)

    def test_force_overrides_a_live_run(self, document_id):
        run_id = _start(document_id)
        _claim(run_id)

        with session_scope() as session:
            forced = create_run(session, document_id=document_id, force=True)
        assert forced.id != run_id


class TestCancellingAStuckRun:
    def test_cancel_terminates_a_run_no_worker_owns(self, document_id, settings, monkeypatch):
        from app.services.runs import cancel_run

        monkeypatch.setattr(settings, "job_stale_after_seconds", 60.0)
        run_id = _start(document_id)
        _age(_claim(run_id), 300)

        with session_scope() as session:
            cancel_run(session, run_id)

        # Requesting cancellation of a job nobody holds used to change nothing.
        assert _status(run_id) is RunStatus.CANCELLED
        _rerun(document_id)  # and the document is usable again


class TestDuplicateUpload:
    def test_uploading_the_same_file_twice_does_not_inherit_a_stuck_run(
        self, settings, monkeypatch
    ):
        monkeypatch.setattr(settings, "job_stale_after_seconds", 60.0)
        with session_scope() as session:
            first, _ = store_document(session, data=neurogen_pdf(), filename="deck.pdf")
            session.flush()
            first_id = first.id
        run_id = _start(first_id)
        _age(_claim(run_id), 300)

        # Content-addressed storage may hand back the same document row.
        with session_scope() as session:
            second, _ = store_document(session, data=neurogen_pdf(), filename="deck-copy.pdf")
            session.flush()
            second_id = second.id

        assert _rerun(second_id), "a re-upload must be analysable"
        if second_id == first_id:
            assert _status(run_id) is RunStatus.FAILED
