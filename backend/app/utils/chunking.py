"""Grouping pages into model-sized batches.

Claim extraction works best with several pages of context at once (a deck
states a result on one slide and its caveat on the next), but a whole deck
exceeds a comfortable context and degrades recall.  These helpers build
overlapping batches that respect a character budget and never split a page.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from app.utils.text import token_estimate, truncate


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


def chunk_pages(
    pages: Sequence[dict[str, Any]],
    *,
    max_chars: int = 24_000,
    overlap: int = 1,
    max_pages_per_chunk: int = 12,
) -> list[PageChunk]:
    """Split ``pages`` into batches under ``max_chars``.

    ``overlap`` repeats the trailing pages of one chunk at the head of the
    next so a claim spanning a page boundary is not lost.  Duplicate claims
    created by the overlap are removed downstream by statement deduplication.
    """
    if not pages:
        return []

    chunks: list[PageChunk] = []
    current: list[dict[str, Any]] = []
    current_chars = 0

    for page in pages:
        page_chars = len(page.get("text", "") or "")
        would_exceed = current and (
            current_chars + page_chars > max_chars or len(current) >= max_pages_per_chunk
        )
        if would_exceed:
            chunks.append(PageChunk(pages=list(current), index=len(chunks)))
            tail = current[-overlap:] if overlap > 0 else []
            current = list(tail)
            current_chars = sum(len(p.get("text", "") or "") for p in current)
        current.append(page)
        current_chars += page_chars

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
