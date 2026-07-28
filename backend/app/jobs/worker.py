"""Background worker process.

Run with ``biointel-worker`` or ``python -m app.jobs.worker``.  Handles
graceful shutdown (finish in-flight work, stop claiming new work), periodic
reaping of jobs abandoned by crashed workers, and heartbeating so that this
worker's own crash is detected.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import signal
import sys
from typing import Any

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_all
from app.jobs import queue
from app.pipeline.orchestrator import AnalysisPipeline, CancelledError

log = get_logger(__name__)


class Worker:
    def __init__(self, concurrency: int | None = None) -> None:
        self.concurrency = concurrency or settings.worker_concurrency
        self.identity = queue.worker_identity()
        self._shutdown = asyncio.Event()
        self._active: set[asyncio.Task[Any]] = set()

    async def run(self) -> None:
        log.info(
            "worker.started",
            worker=self.identity,
            concurrency=self.concurrency,
            provider=settings.llm_provider,
        )
        # Recover before claiming anything. A worker starting up is the most
        # likely moment for abandoned work to exist -- it usually means the
        # previous one died -- and waiting half a stale-timeout to notice
        # leaves the affected documents locked for no reason.
        await self._recover()

        reaper = asyncio.create_task(self._reap_loop())
        try:
            while not self._shutdown.is_set():
                if len(self._active) >= self.concurrency:
                    await self._wait_for_slot()
                    continue

                job = await asyncio.to_thread(
                    functools.partial(
                        queue.claim_next,
                        self.identity,
                        job_types=[queue.JOB_ANALYSE_DOCUMENT],
                    )
                )
                if job is None:
                    await self._sleep(settings.job_poll_interval_seconds)
                    continue

                task = asyncio.create_task(self._run_job(job))
                self._active.add(task)
                task.add_done_callback(self._active.discard)
        finally:
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper
            if self._active:
                log.info("worker.draining", in_flight=len(self._active))
                await asyncio.gather(*self._active, return_exceptions=True)
            log.info("worker.stopped", worker=self.identity)

    async def _wait_for_slot(self) -> None:
        if not self._active:
            return
        await asyncio.wait(self._active, return_when=asyncio.FIRST_COMPLETED)

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._shutdown.wait(), timeout=seconds)

    async def _reap_loop(self) -> None:
        while not self._shutdown.is_set():
            await self._sleep(settings.job_stale_after_seconds / 2)
            if self._shutdown.is_set():
                return
            await self._recover()

    async def _recover(self) -> None:
        """Re-queue abandoned jobs and unlock runs nothing can finish."""
        try:
            await asyncio.to_thread(queue.reap_stale_jobs)
            await asyncio.to_thread(queue.recover_orphaned_runs)
        except Exception:  # pragma: no cover
            log.warning("worker.reap_failed", exc_info=True)

    async def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        run_id = job["payload"].get("run_id") or job.get("run_id")
        log.info("job.started", job_id=job_id, run_id=run_id, attempt=job["attempts"])

        cancelled = False

        def should_cancel() -> bool:
            return cancelled

        heartbeat_task = asyncio.create_task(self._heartbeat(job_id))
        try:
            pipeline = AnalysisPipeline(should_cancel=should_cancel)
            await pipeline.run(run_id)
            await asyncio.to_thread(queue.mark_succeeded, job_id)
            log.info("job.succeeded", job_id=job_id, run_id=run_id)
        except CancelledError:
            await asyncio.to_thread(queue.mark_cancelled, job_id)
            log.info("job.cancelled", job_id=job_id, run_id=run_id)
        except asyncio.CancelledError:
            # The worker is going down mid-analysis. Hand the job back so the
            # next worker starts it immediately instead of the document
            # sitting locked until the stale-job timeout expires.
            await asyncio.to_thread(
                queue.release, job_id, "Worker shut down while this job was running."
            )
            log.info("job.released_on_shutdown", job_id=job_id, run_id=run_id)
            raise
        except Exception as exc:
            requeued = await asyncio.to_thread(
                queue.mark_failed, job_id, f"{type(exc).__name__}: {exc}"
            )
            log.error(
                "job.failed",
                job_id=job_id,
                run_id=run_id,
                requeued=requeued,
                error=str(exc)[:500],
            )
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _heartbeat(self, job_id: str) -> None:
        """Keep the job marked alive; surface cancellation requests."""
        while True:
            await asyncio.sleep(settings.job_heartbeat_seconds)
            alive = await asyncio.to_thread(queue.heartbeat, job_id)
            if not alive:
                log.info("job.cancel_requested", job_id=job_id)
                return

    def request_shutdown(self) -> None:
        if not self._shutdown.is_set():
            log.info("worker.shutdown_requested")
            self._shutdown.set()


async def _main_async() -> int:
    configure_logging()
    create_all()
    worker = Worker()

    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signal_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, worker.request_shutdown)
        except NotImplementedError:
            # Windows: fall back to the default handler and KeyboardInterrupt.
            signal.signal(sig, lambda *_: worker.request_shutdown())

    try:
        await worker.run()
    except KeyboardInterrupt:  # pragma: no cover
        worker.request_shutdown()
    return 0


def main() -> int:
    try:
        return asyncio.run(_main_async())
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
