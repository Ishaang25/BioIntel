"""Approximate token pricing for cost telemetry.

These figures are used for in-product cost estimates only; they are not a
billing source of truth and are deliberately easy to override via
``BIOINTEL_PRICE_OVERRIDES`` (JSON) if list prices change.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from app.llm.base import Usage


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per 1M tokens."""

    input: float
    cached_input: float
    output: float


#: Prefix-matched so dated snapshots (``gpt-5-2025-xx-xx``) inherit prices.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "gpt-5-nano": ModelPrice(0.05, 0.005, 0.40),
    "gpt-5-mini": ModelPrice(0.25, 0.025, 2.00),
    "gpt-5": ModelPrice(1.25, 0.125, 10.00),
    "gpt-4.1-nano": ModelPrice(0.10, 0.025, 0.40),
    "gpt-4.1-mini": ModelPrice(0.40, 0.10, 1.60),
    "gpt-4.1": ModelPrice(2.00, 0.50, 8.00),
    "gpt-4o-mini": ModelPrice(0.15, 0.075, 0.60),
    "gpt-4o": ModelPrice(2.50, 1.25, 10.00),
    "o4-mini": ModelPrice(1.10, 0.275, 4.40),
    "o3": ModelPrice(2.00, 0.50, 8.00),
    "text-embedding-3-small": ModelPrice(0.02, 0.02, 0.0),
    "text-embedding-3-large": ModelPrice(0.13, 0.13, 0.0),
}


def _load_overrides() -> dict[str, ModelPrice]:
    raw = os.environ.get("BIOINTEL_PRICE_OVERRIDES")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    out: dict[str, ModelPrice] = {}
    for model, price in data.items():
        try:
            out[model] = ModelPrice(
                float(price["input"]),
                float(price.get("cached_input", price["input"])),
                float(price["output"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def price_for(model: str) -> ModelPrice | None:
    prices = {**DEFAULT_PRICES, **_load_overrides()}
    if model in prices:
        return prices[model]
    # Longest prefix wins so "gpt-5-mini-2025-01-01" beats "gpt-5".
    candidates = [key for key in prices if model.startswith(key)]
    if not candidates:
        return None
    return prices[max(candidates, key=len)]


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Estimated spend for one model's usage, split by what was billed."""

    #: Uncached prompt tokens.
    input_usd: float = 0.0
    #: Prompt tokens served from the provider's cache, billed at a discount.
    cached_input_usd: float = 0.0
    #: Completion tokens. Reasoning tokens are billed at the completion rate
    #: and are already counted inside ``Usage.output_tokens`` by the provider,
    #: so they are reported separately for visibility, not added again.
    output_usd: float = 0.0
    reasoning_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return self.input_usd + self.cached_input_usd + self.output_usd

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        return CostBreakdown(
            input_usd=self.input_usd + other.input_usd,
            cached_input_usd=self.cached_input_usd + other.cached_input_usd,
            output_usd=self.output_usd + other.output_usd,
            reasoning_usd=self.reasoning_usd + other.reasoning_usd,
        )


def cost_breakdown(model: str, usage: Usage) -> CostBreakdown:
    """Split estimated spend into its billed components.

    ``estimate_cost_usd`` returns only the total; the breakdown is what the
    run summary reports, so it is derived from the same prices rather than
    apportioned after the fact.
    """
    price = price_for(model)
    if price is None:
        return CostBreakdown()
    fresh_input = max(0, usage.input_tokens - usage.cached_input_tokens)
    return CostBreakdown(
        input_usd=fresh_input * price.input / 1_000_000,
        cached_input_usd=usage.cached_input_tokens * price.cached_input / 1_000_000,
        output_usd=usage.output_tokens * price.output / 1_000_000,
        reasoning_usd=usage.reasoning_tokens * price.output / 1_000_000,
    )


def estimate_cost_usd(model: str, usage: Usage) -> float:
    return round(cost_breakdown(model, usage).total_usd, 6)
