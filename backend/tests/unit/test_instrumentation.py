"""Tests for the run instrumentation layer.

The metrics artefacts are what the benchmark and regression tooling reads, so
their shape is a contract: a field that silently stops being populated would
make a regression look like a code change.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.core.instrumentation import RunMetrics, StageMetrics
from app.llm.base import Usage
from app.llm.pricing import cost_breakdown, estimate_cost_usd


def _metrics(**overrides) -> RunMetrics:
    base = {
        "run_id": "run_test",
        "document_id": "doc_test",
        "status": "succeeded",
        "started_at": dt.datetime(2026, 7, 28, 12, 0, 0, tzinfo=dt.UTC),
    }
    return RunMetrics(**{**base, **overrides})


class TestSerialisation:
    def test_to_dict_renders_datetimes_as_iso_strings(self):
        metrics = _metrics(finished_at=dt.datetime(2026, 7, 28, 12, 5, 0, tzinfo=dt.UTC))
        data = metrics.to_dict()

        assert data["started_at"] == "2026-07-28T12:00:00+00:00"
        assert data["finished_at"] == "2026-07-28T12:05:00+00:00"

    def test_to_json_round_trips(self):
        metrics = _metrics(
            total_runtime_ms=818_000,
            claims_total=63,
            stages=[StageMetrics(stage="parse", status="succeeded", duration_ms=17_000)],
        )

        data = json.loads(metrics.to_json())

        assert data["claims_total"] == 63
        assert data["stages"][0]["stage"] == "parse"
        assert data["stages"][0]["duration_ms"] == 17_000

    def test_unfinished_run_serialises_with_a_null_finish(self):
        data = _metrics(status="running").to_dict()

        assert data["finished_at"] is None

    def test_save_writes_both_artefacts(self, tmp_path):
        metrics = _metrics(
            stages=[StageMetrics(stage="parse", status="succeeded", duration_ms=1000)]
        )

        metrics.save_json(tmp_path / "nested" / "run_metrics.json")
        metrics.save_summary_markdown(tmp_path / "nested" / "run_summary.md")

        assert json.loads((tmp_path / "nested" / "run_metrics.json").read_text())["run_id"] == (
            "run_test"
        )
        assert "# BioIntel Analysis Summary" in (tmp_path / "nested" / "run_summary.md").read_text()


class TestSummaryRendering:
    @pytest.mark.parametrize(
        ("runtime_ms", "expected"),
        [
            (17_000, "17s"),
            (818_000, "13m 38s"),
            (3_661_000, "1h 01m 01s"),
            (0, "0s"),
        ],
    )
    def test_runtime_is_humanised(self, runtime_ms, expected):
        summary = _metrics(total_runtime_ms=runtime_ms).to_summary_markdown()

        assert f"**Total Runtime:** {expected}" in summary

    def test_every_stage_appears_in_the_execution_profile(self):
        metrics = _metrics(
            stages=[
                StageMetrics(stage="parse", status="succeeded", duration_ms=17_000),
                StageMetrics(stage="claims", status="succeeded", duration_ms=342_000),
                StageMetrics(stage="retrieval", status="degraded", duration_ms=123_000),
            ]
        )

        summary = metrics.to_summary_markdown()

        assert "| parse | 17s | succeeded |" in summary
        assert "| claims | 5m 42s | succeeded |" in summary
        assert "| retrieval | 2m 03s | degraded |" in summary

    def test_coverage_and_confidence_are_reported(self):
        summary = _metrics(
            verification_coverage=0.4285,
            assessment_confidence=0.67,
        ).to_summary_markdown()

        assert "- Verification Coverage: 42.9%" in summary
        assert "- Assessment Confidence: 0.67" in summary

    def test_degraded_runs_are_called_out(self):
        assert "Degraded Mode" in _metrics(degraded=True).to_summary_markdown()
        assert "Degraded Mode" not in _metrics(degraded=False).to_summary_markdown()

    def test_warnings_are_listed(self):
        summary = _metrics(
            warnings=["retrieval degraded", "3 claims truncated"]
        ).to_summary_markdown()

        assert "- retrieval degraded" in summary
        assert "- 3 claims truncated" in summary

    def test_offline_runs_name_the_absent_provider(self):
        assert "none (offline mode)" in _metrics(llm_provider=None).to_summary_markdown()


class TestCostBreakdown:
    """The reported split must come from real prices, not an assumed ratio."""

    def test_components_sum_to_the_total(self):
        usage = Usage(input_tokens=318_000, output_tokens=102_000, cached_input_tokens=64_000)

        breakdown = cost_breakdown("gpt-5", usage)

        assert breakdown.total_usd == pytest.approx(estimate_cost_usd("gpt-5", usage), abs=1e-6)

    def test_input_and_output_are_priced_independently(self):
        # gpt-5 output tokens cost 8x its input tokens; a 50/50 split would
        # hide exactly this.
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)

        breakdown = cost_breakdown("gpt-5", usage)

        assert breakdown.input_usd == pytest.approx(1.25)
        assert breakdown.output_usd == pytest.approx(10.00)
        assert breakdown.input_usd != pytest.approx(breakdown.output_usd)

    def test_cached_input_is_billed_at_the_cached_rate(self):
        usage = Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000)

        breakdown = cost_breakdown("gpt-5", usage)

        assert breakdown.input_usd == pytest.approx(0.0)
        assert breakdown.cached_input_usd == pytest.approx(0.125)

    def test_reasoning_is_reported_but_not_double_counted(self):
        # Providers count reasoning tokens inside output_tokens; adding them
        # again would overstate spend.
        usage = Usage(input_tokens=0, output_tokens=1_000_000, reasoning_tokens=400_000)

        breakdown = cost_breakdown("gpt-5", usage)

        assert breakdown.reasoning_usd == pytest.approx(4.00)
        assert breakdown.total_usd == pytest.approx(10.00)

    def test_unknown_models_cost_nothing_rather_than_guessing(self):
        breakdown = cost_breakdown("some-unreleased-model", Usage(input_tokens=1_000_000))

        assert breakdown.total_usd == 0.0

    def test_breakdowns_accumulate(self):
        one = cost_breakdown("gpt-5", Usage(input_tokens=1_000_000))
        two = cost_breakdown("gpt-5-mini", Usage(output_tokens=1_000_000))

        combined = one + two

        assert combined.input_usd == pytest.approx(1.25)
        assert combined.output_usd == pytest.approx(2.00)
        assert combined.total_usd == pytest.approx(3.25)
