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


def estimate_cost_usd(model: str, usage: Usage) -> float:
    price = price_for(model)
    if price is None:
        return 0.0
    fresh_input = max(0, usage.input_tokens - usage.cached_input_tokens)
    total = (
        fresh_input * price.input
        + usage.cached_input_tokens * price.cached_input
        + usage.output_tokens * price.output
    ) / 1_000_000
    return round(total, 6)
