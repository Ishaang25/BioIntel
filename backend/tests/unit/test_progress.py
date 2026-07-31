"""Intra-stage progress reporting.

The reported symptom was a UI stuck on "Page Understanding". The pipeline was
healthy; progress simply did not move until a stage finished, and that stage
carries 18% of the weight and runs once per page of the deck.
"""

from __future__ import annotations

from app.core.enums import STAGE_WEIGHTS, PipelineStage
from app.pipeline.progress import MIN_INTERVAL_SECONDS, StageProgress, stage_start


class Recorder:
    def __init__(self) -> None:
        self.writes: list[tuple[PipelineStage, float]] = []

    def __call__(self, run_id: str, stage: PipelineStage, progress: float) -> None:
        self.writes.append((stage, progress))


def _reporter(sink: Recorder) -> StageProgress:
    reporter = StageProgress("run_x", sink)
    # Defeat the time-based throttle so the delta rule can be tested alone.
    reporter._last_time = -MIN_INTERVAL_SECONDS * 10
    return reporter


class TestStageStart:
    def test_first_stage_starts_at_zero(self) -> None:
        assert stage_start(PipelineStage.PARSE) == 0.0

    def test_later_stage_starts_after_preceding_weights(self) -> None:
        expected = STAGE_WEIGHTS[PipelineStage.PARSE]
        assert stage_start(PipelineStage.PAGE_UNDERSTANDING) == expected


class TestStageProgress:
    def test_advances_within_the_stage_band(self) -> None:
        sink = Recorder()
        reporter = _reporter(sink)
        stage = PipelineStage.PAGE_UNDERSTANDING
        start = stage_start(stage)
        end = start + STAGE_WEIGHTS[stage]

        reporter.advance(stage, 25, 25)

        assert sink.writes, "completing a stage must write progress"
        _, value = sink.writes[-1]
        assert value == round(end, 4)
        assert start < value <= 1.0

    def test_halfway_lands_halfway_through_the_band(self) -> None:
        sink = Recorder()
        stage = PipelineStage.PAGE_UNDERSTANDING
        start = stage_start(stage)
        _reporter(sink).advance(stage, 12, 24)
        _, value = sink.writes[-1]
        assert value == round(start + STAGE_WEIGHTS[stage] * 0.5, 4)

    def test_progress_moves_across_a_realistic_page_run(self) -> None:
        """The regression: a 25-page deck must produce a moving bar."""
        sink = Recorder()
        reporter = StageProgress("run_x", sink)
        stage = PipelineStage.PAGE_UNDERSTANDING

        for page in range(1, 26):
            reporter._last_time = -MIN_INTERVAL_SECONDS * 10  # bypass throttle
            reporter.advance(stage, page, 25)

        values = [value for _, value in sink.writes]
        assert len(values) >= 10, "the bar should tick many times across 25 pages"
        assert values == sorted(values), "progress must never go backwards"
        assert values[-1] == round(stage_start(stage) + STAGE_WEIGHTS[stage], 4)

    def test_final_tick_is_always_written_even_when_throttled(self) -> None:
        sink = Recorder()
        reporter = StageProgress("run_x", sink)  # throttle fully active
        reporter.advance(PipelineStage.PAGE_UNDERSTANDING, 25, 25)
        assert len(sink.writes) == 1, "the completing tick must never be dropped"

    def test_throttle_suppresses_intermediate_writes(self) -> None:
        """A 200-page deck must not issue one UPDATE per page."""
        sink = Recorder()
        reporter = StageProgress("run_x", sink)
        for page in range(1, 200):
            reporter.advance(PipelineStage.PAGE_UNDERSTANDING, page, 200)
        assert len(sink.writes) <= 5, f"throttle leaked {len(sink.writes)} writes"

    def test_zero_total_is_ignored(self) -> None:
        sink = Recorder()
        _reporter(sink).advance(PipelineStage.PAGE_UNDERSTANDING, 0, 0)
        assert sink.writes == []

    def test_a_failing_sink_never_breaks_the_run(self) -> None:
        def explode(run_id: str, stage: PipelineStage, progress: float) -> None:
            raise RuntimeError("database is down")

        reporter = StageProgress("run_x", explode)
        reporter.advance(PipelineStage.PAGE_UNDERSTANDING, 1, 1)  # must not raise
