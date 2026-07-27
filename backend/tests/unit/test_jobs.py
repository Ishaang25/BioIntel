"""Job queue semantics: claiming, retries, cancellation, crash recovery."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.enums import JobStatus
from app.db.models import Job
from app.db.session import session_scope
from app.jobs import queue


@pytest.fixture
def job_id() -> str:
    return queue.enqueue(queue.JOB_ANALYSE_DOCUMENT, {"run_id": "run_1"}, run_id="run_1")


class TestEnqueue:
    def test_job_starts_queued(self, job_id):
        with session_scope() as session:
            job = session.get(Job, job_id)
        assert job.status is JobStatus.QUEUED
        assert job.attempts == 0
        assert job.payload == {"run_id": "run_1"}

    def test_delayed_job_is_not_immediately_due(self):
        queue.enqueue("t", {}, delay_seconds=3600)
        assert queue.claim_next("worker-1") is None


class TestClaiming:
    def test_claim_marks_running_and_increments_attempts(self, job_id):
        claimed = queue.claim_next("worker-1")
        assert claimed is not None
        assert claimed["id"] == job_id
        assert claimed["attempts"] == 1

        with session_scope() as session:
            job = session.get(Job, job_id)
        assert job.status is JobStatus.RUNNING
        assert job.locked_by == "worker-1"
        assert job.heartbeat_at is not None

    def test_a_job_is_claimed_exactly_once(self, job_id):
        assert queue.claim_next("worker-1") is not None
        assert queue.claim_next("worker-2") is None

    def test_higher_priority_is_claimed_first(self):
        queue.enqueue("t", {"n": 1}, priority=200)
        queue.enqueue("t", {"n": 2}, priority=10)
        assert queue.claim_next("w")["payload"] == {"n": 2}

    def test_job_type_filter_is_respected(self):
        queue.enqueue("other_type", {})
        assert queue.claim_next("w", job_types=[queue.JOB_ANALYSE_DOCUMENT]) is None
        assert queue.claim_next("w", job_types=["other_type"]) is not None

    def test_empty_queue_returns_none(self):
        assert queue.claim_next("worker-1") is None


class TestCompletion:
    def test_success(self, job_id):
        queue.claim_next("w")
        queue.mark_succeeded(job_id)
        with session_scope() as session:
            job = session.get(Job, job_id)
        assert job.status is JobStatus.SUCCEEDED
        assert job.locked_by is None

    def test_failure_is_retried_with_backoff(self, job_id):
        queue.claim_next("w")
        assert queue.mark_failed(job_id, "boom") is True
        with session_scope() as session:
            job = session.get(Job, job_id)
        assert job.status is JobStatus.QUEUED
        assert job.last_error == "boom"
        assert job.run_after > dt.datetime.now(dt.UTC)

    def test_failure_is_terminal_after_max_attempts(self):
        job_id = queue.enqueue("t", {}, max_attempts=2)
        for _ in range(2):
            queue.claim_next("w")
            queue.mark_failed(job_id, "boom")
            with session_scope() as session:
                session.get(Job, job_id).run_after = dt.datetime.now(dt.UTC)
        with session_scope() as session:
            assert session.get(Job, job_id).status is JobStatus.FAILED

    def test_retry_can_be_disabled(self, job_id):
        queue.claim_next("w")
        assert queue.mark_failed(job_id, "fatal", retry=False) is False
        with session_scope() as session:
            assert session.get(Job, job_id).status is JobStatus.FAILED


class TestCancellation:
    def test_cancel_sets_the_flag_and_heartbeat_reports_it(self, job_id):
        queue.claim_next("w")
        assert queue.request_cancel("run_1") == 1
        assert queue.is_cancelled(job_id) is True
        assert queue.heartbeat(job_id) is False

    def test_heartbeat_returns_true_while_alive(self, job_id):
        queue.claim_next("w")
        assert queue.heartbeat(job_id) is True

    def test_cancelling_an_unknown_run_is_a_no_op(self):
        assert queue.request_cancel("run_missing") == 0

    def test_mark_cancelled(self, job_id):
        queue.claim_next("w")
        queue.mark_cancelled(job_id)
        with session_scope() as session:
            assert session.get(Job, job_id).status is JobStatus.CANCELLED


class TestCrashRecovery:
    def test_stale_job_is_requeued(self, job_id, settings, monkeypatch):
        queue.claim_next("worker-that-died")
        monkeypatch.setattr(settings, "job_stale_after_seconds", 0.0)

        assert queue.reap_stale_jobs() == 1
        with session_scope() as session:
            job = session.get(Job, job_id)
        assert job.status is JobStatus.QUEUED
        assert job.locked_by is None
        assert "stopped responding" in job.last_error

        # The requeued job is claimable again.
        assert queue.claim_next("worker-2") is not None

    def test_live_job_is_not_reaped(self, job_id):
        queue.claim_next("worker-1")
        assert queue.reap_stale_jobs() == 0

    def test_stale_job_out_of_attempts_fails(self, settings, monkeypatch):
        job_id = queue.enqueue("t", {}, max_attempts=1)
        queue.claim_next("w")
        monkeypatch.setattr(settings, "job_stale_after_seconds", 0.0)
        queue.reap_stale_jobs()
        with session_scope() as session:
            assert session.get(Job, job_id).status is JobStatus.FAILED


class TestIntrospection:
    def test_queue_depth_counts_by_status(self, job_id):
        queue.enqueue("t", {})
        queue.claim_next("w")
        depth = queue.queue_depth()
        assert depth.get("running") == 1
        assert depth.get("queued") == 1

    def test_worker_identity_is_stable(self):
        assert queue.worker_identity() == queue.worker_identity()
        assert ":" in queue.worker_identity()
