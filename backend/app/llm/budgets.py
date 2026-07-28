"""Output-token budgets that account for reasoning.

``max_output_tokens`` on the Responses API is a ceiling on *everything* the
model generates -- the reasoning it does before answering **and** the visible
answer.  Sizing it from the expected answer alone is the mistake that broke
claim extraction:

    claims  gpt-5  in 4,814  out 6,000 = 4,928 reasoning + 1,072 content
    claims  gpt-5  empty response
    claims  gpt-5  empty response
    claims  gpt-5  empty response

A 6,000-token cap chosen to bound a chunk's *answer* was consumed by the
model's reasoning, so three of four chunks returned nothing at all and the
fourth was cut off after a fraction of its claims.  The stage then reported
"no verifiable scientific claims" about a deck full of Phase 3 readouts.

So a budget here is two numbers: room for the answer, plus a reserve for the
thinking that precedes it.  The reserve is zero for non-reasoning models, and
scales with ``reasoning_effort`` otherwise.
"""

from __future__ import annotations

from app.core.config import settings

__all__ = ["RESERVE_BY_EFFORT", "output_budget", "reasoning_reserve"]

#: Tokens to reserve for reasoning, by effort. Derived from observed usage:
#: gpt-5 at medium effort spent 4,928 reasoning tokens on one 8-page claim
#: chunk, so "medium" must leave roughly double that to be safe.
RESERVE_BY_EFFORT: dict[str, int] = {
    "minimal": 1_000,
    "low": 4_000,
    "medium": 10_000,
    "high": 20_000,
}

#: Used when a model advertises reasoning but the effort is unrecognised.
DEFAULT_RESERVE = 10_000


def _is_reasoning_model(model: str) -> bool:
    # Imported lazily: the provider module pulls in the OpenAI SDK, and the
    # offline path must not require it.
    from app.llm.openai_provider import is_reasoning_model

    return is_reasoning_model(model)


def reasoning_reserve(model: str, effort: str | None) -> int:
    """Tokens the model may spend thinking before it writes anything."""
    if not _is_reasoning_model(model):
        return 0
    key = (effort or settings.llm_reasoning_effort or "").lower()
    return RESERVE_BY_EFFORT.get(key, DEFAULT_RESERVE)


def output_budget(model: str, *, content_tokens: int, effort: str | None = None) -> int:
    """The ``max_output_tokens`` to send for an answer of ``content_tokens``.

    Clamped to :attr:`Settings.llm_max_output_tokens` so the global cost guard
    still binds. A caller that needs more visible output must reduce the
    reasoning effort or shrink the request -- not silently overrun the ceiling.
    """
    total = content_tokens + reasoning_reserve(model, effort)
    return max(content_tokens, min(total, settings.llm_max_output_tokens))
