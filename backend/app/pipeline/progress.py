"""Intra-stage progress reporting.

Progress used to move only at stage boundaries. ``page_understanding`` carries
18% of the total weight and processes every page of the deck, so on a large one
the bar sat at 6% for minutes while the backend worked normally -- the pipeline
was healthy and the UI said nothing was happening. Users read that as frozen,
which is a reporting defect rather than a performance one.

A stage that iterates over a known number of items reports as it goes, so the
bar advances continuously inside the stage's own weight band.

Writes are throttled. A 200-page deck completing a page every few hundred
milliseconds would otherwise issue one UPDATE per page against the same row,
and progress that is accurate to a tenth of a percent is worth nothing to the
person watching it.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from app.core.enums import STAGE_WEIGHTS, PipelineStage
from app.core.logging import get_logger

log = get_logger(__name__)

#: Minimum change in overall progress before a write is worth making.
MIN_DELTA = 0.005

#: Minimum seconds between writes for the same stage, whatever the delta.
MIN_INTERVAL_SECONDS = 1.0


class ProgressSink(Protocol):
    """Persists a run's progress. Kept abstract so tests need no database."""

    def __call__(self, run_id: str, stage: PipelineStage, progress: float) -> None: ...


class ProgressReporter(Protocol):
    """What a stage sees: somewhere to say how far through it is."""

    def advance(self, stage: PipelineStage, completed: int, total: int) -> None: ...


class StageProgress:
    """Reports completion within a stage, throttled.

    Thread-safe: the pipeline is asyncio, but stage work is dispatched across a
    thread pool for the synchronous persistence layer, and callbacks can arrive
    from either.
    """

    def __init__(self, run_id: str, sink: ProgressSink, base: float | None = None) -> None:
        self._run_id = run_id
        self._sink = sink
        self._base = base
        self._lock = threading.Lock()
        self._last_written = -1.0
        self._last_time = 0.0

    def advance(self, stage: PipelineStage, completed: int, total: int) -> None:
        """Record `completed` of `total` items done within `stage`."""
        if total <= 0:
            return

        fraction = min(1.0, max(0.0, completed / total))
        start = self._base if self._base is not None else stage_start(stage)
        overall = round(start + STAGE_WEIGHTS.get(stage, 0.0) * fraction, 4)

        now = time.monotonic()
        with self._lock:
            moved_enough = overall - self._last_written >= MIN_DELTA
            waited_enough = now - self._last_time >= MIN_INTERVAL_SECONDS
            complete = completed >= total
            # Always write the final tick: the last item of a long stage is the
            # one the reader is waiting on.
            if not complete and not (moved_enough and waited_enough):
                return
            self._last_written = overall
            self._last_time = now

        try:
            self._sink(self._run_id, stage, overall)
        except Exception:  # pragma: no cover - progress must never fail a run
            log.warning("progress.write_failed", stage=stage.value, exc_info=True)


def stage_start(stage: PipelineStage) -> float:
    """Cumulative weight of every stage before `stage`."""
    total = 0.0
    for candidate in PipelineStage:
        if candidate is stage:
            break
        total += STAGE_WEIGHTS.get(candidate, 0.0)
    return round(total, 4)


class NullProgress:
    """No-op reporter, for code paths with no run to report against."""

    def advance(self, stage: PipelineStage, completed: int, total: int) -> None:
        return
