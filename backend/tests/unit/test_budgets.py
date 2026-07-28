"""Output budgets must leave room for reasoning.

``max_output_tokens`` bounds reasoning tokens *and* visible output together.
Sizing it from the expected answer is what broke claim extraction on the
Moderna deck:

    claims  gpt-5  in 4,814  out 6,000 = 4,928 reasoning + 1,072 content
    claims  gpt-5  empty response  x 3

Three chunks produced nothing at all, the fourth was cut off, and a deck full
of Phase 3 readouts was reported as containing no verifiable scientific
claims.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.llm.budgets import RESERVE_BY_EFFORT, output_budget, reasoning_reserve


class TestReasoningReserve:
    def test_non_reasoning_models_reserve_nothing(self):
        assert reasoning_reserve("gpt-4o-mini", "medium") == 0

    @pytest.mark.parametrize("effort", sorted(RESERVE_BY_EFFORT))
    def test_reasoning_models_reserve_by_effort(self, effort):
        assert reasoning_reserve("gpt-5", effort) == RESERVE_BY_EFFORT[effort]

    def test_more_effort_reserves_more(self):
        assert (
            reasoning_reserve("gpt-5", "minimal")
            < reasoning_reserve("gpt-5", "low")
            < reasoning_reserve("gpt-5", "medium")
            < reasoning_reserve("gpt-5", "high")
        )

    def test_unknown_effort_falls_back_to_a_safe_reserve(self):
        assert reasoning_reserve("gpt-5", "enthusiastic") >= RESERVE_BY_EFFORT["medium"]


class TestOutputBudget:
    def test_budget_exceeds_the_content_it_must_carry(self):
        """The regression in one assertion."""
        budget = output_budget("gpt-5", content_tokens=6_000, effort="medium")
        assert budget > 6_000, "the answer would have to share its budget with the reasoning"

    def test_budget_covers_observed_reasoning_usage(self):
        """gpt-5 spent 4,928 reasoning tokens on one 8-page claim chunk."""
        budget = output_budget("gpt-5", content_tokens=6_000, effort="medium")
        assert budget - 6_000 >= 4_928

    def test_non_reasoning_models_are_not_padded(self):
        assert output_budget("gpt-4o-mini", content_tokens=6_000, effort="medium") == 6_000

    def test_budget_never_exceeds_the_global_ceiling(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_max_output_tokens", 8_000)
        assert output_budget("gpt-5", content_tokens=6_000, effort="high") == 8_000

    def test_content_is_never_squeezed_below_what_was_asked_for(self, monkeypatch):
        """Clamping must not silently hand back less room than requested."""
        monkeypatch.setattr(settings, "llm_max_output_tokens", 1_000)
        assert output_budget("gpt-5", content_tokens=6_000, effort="high") == 6_000


class TestConfiguredStagesHaveHeadroom:
    """The settings the pipeline actually ships with must not starve a model."""

    def test_claim_extraction_has_reasoning_headroom(self):
        budget = output_budget(
            settings.model_reasoning,
            content_tokens=settings.extraction_chunk_output_tokens,
            effort=settings.llm_reasoning_effort,
        )
        assert budget >= settings.extraction_chunk_output_tokens + 4_928

    def test_entity_extraction_has_reasoning_headroom(self):
        budget = output_budget(
            settings.model_extraction,
            content_tokens=settings.extraction_chunk_output_tokens,
            effort=settings.llm_extraction_reasoning_effort,
        )
        assert budget > settings.extraction_chunk_output_tokens

    def test_page_understanding_has_reasoning_headroom(self):
        budget = output_budget(
            settings.model_vision,
            content_tokens=settings.vision_max_output_tokens,
            effort=settings.vision_reasoning_effort,
        )
        assert budget > settings.vision_max_output_tokens
