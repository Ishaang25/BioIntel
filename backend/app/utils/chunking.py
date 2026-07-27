"""Grouping pages into model-sized batches.

Extraction works best with several pages of context at once (a deck states a
result on one slide and its caveat on the next), but a whole deck exceeds a
comfortable context, degrades recall, and -- the failure that motivated this
module's token budget -- produces an answer too long to fit in the output
window, which comes back as invalid JSON.  These helpers build overlapping
batches that respect a token budget and never split a page.

Budgets are expressed in tokens rather than characters because the ceiling
that actually bites is the model's, and because a page's composite text
(text layer + tables + vision-recovered content) is several times the size of
its raw text.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from app.utils.text import token_estimate, truncate

#: Prompt template, system message and per-page headers all consume input
#: budget that the page text itself does not account for.
PROMPT_OVERHEAD_TOKENS = 1_200


@dataclass(slots=True)
class PageChunk:
    """A contiguous group of pages small enough for one model call."""

    pages: list[dict[str, Any]]
    index: int

    @property
    def page_numbers(self) -> list[int]:
        return [int(p.get("page_number", 0)) for p in self.pages]

    @property
    def char_count(self) -> int:
        return sum(len(p.get("text", "") or "") for p in self.pages)

    @property
    def token_estimate(self) -> int:
        return sum(token_estimate(p.get("text", "") or "") for p in self.pages)

    def split(self) -> list[PageChunk]:
        """Halve this chunk, for retrying a call that produced too much output."""
        if len(self.pages) < 2:
            return []
        middle = len(self.pages) // 2
        return [
            PageChunk(pages=self.pages[:middle], index=self.index),
            PageChunk(pages=self.pages[middle:], index=self.index),
        ]


def chunk_pages(
    pages: Sequence[dict[str, Any]],
    *,
    max_tokens: int = 6_000,
    overlap: int = 1,
    max_pages_per_chunk: int = 8,
) -> list[PageChunk]:
    """Split ``pages`` into batches under ``max_tokens``.

    ``overlap`` repeats the trailing pages of one chunk at the head of the
    next so a claim spanning a page boundary is not lost.  Duplicates created
    by the overlap are removed downstream by statement/entity deduplication.

    A single page larger than the budget still gets its own chunk: pages are
    never split, because a quote must remain attributable to one page.
    """
    if not pages:
        return []

    chunks: list[PageChunk] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0

    for page in pages:
        page_tokens = token_estimate(page.get("text", "") or "")
        would_exceed = current and (
            current_tokens + page_tokens > max_tokens or len(current) >= max_pages_per_chunk
        )
        if would_exceed:
            chunks.append(PageChunk(pages=list(current), index=len(chunks)))
            tail = current[-overlap:] if overlap > 0 else []
            tail_tokens = sum(token_estimate(p.get("text", "") or "") for p in tail)
            if tail_tokens >= max_tokens:
                # Carrying an oversized page forward would make every
                # subsequent chunk oversized too; lose the overlap instead.
                tail, tail_tokens = [], 0
            current = list(tail)
            current_tokens = tail_tokens
        current.append(page)
        current_tokens += page_tokens

    if current:
        chunks.append(PageChunk(pages=current, index=len(chunks)))
    return chunks


def render_pages_for_prompt(pages: Sequence[dict[str, Any]], *, per_page_limit: int = 8000) -> str:
    """Format pages as labelled blocks for a prompt."""
    blocks: list[str] = []
    for page in pages:
        number = page.get("page_number", "?")
        title = (page.get("slide_title") or "").strip()
        header = f"=== PAGE {number}" + (f" — {title}" if title else "") + " ==="
        body = truncate((page.get("text") or "").strip(), per_page_limit)
        blocks.append(f"{header}\n{body if body else '(no extractable content)'}")
    return "\n\n".join(blocks)


def iter_batches(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def estimate_prompt_tokens(text: str) -> int:
    return token_estimate(text)
