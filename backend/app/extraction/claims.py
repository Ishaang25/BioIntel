"""Stage 5: scientific claim extraction with verified provenance.

The contract this module enforces is what makes the rest of the product
trustworthy: **every claim carries a quote that provably appears in the
document.**  A claim whose quote cannot be located is not silently kept -- it
is either downgraded and flagged for human review, or dropped.

Pipeline within the stage:

    page batches -> parallel extraction -> quote verification
                 -> cross-batch deduplication -> importance re-ranking
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.core.config import settings
from app.core.enums import ClaimCategory, QuoteVerification
from app.core.logging import get_logger
from app.extraction import lexicon
from app.llm import prompts
from app.llm.client import LLMClient
from app.llm.schemas import ClaimExtractionOut, ExtractedClaim
from app.utils.chunking import chunk_pages, render_pages_for_prompt
from app.utils.text import collapse_whitespace, normalize_for_match, verify_quote

log = get_logger(__name__)

#: Two claims whose statements match above this are treated as one.
DEDUPE_THRESHOLD = 88.0
#: Claims below this extraction confidence are flagged rather than trusted.
LOW_CONFIDENCE = 0.45


@dataclass(slots=True)
class VerifiedClaim:
    """An extracted claim after provenance verification."""

    claim: ExtractedClaim
    quote_verification: QuoteVerification
    quote_match_score: float
    needs_human_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    from_visual: bool = False

    @property
    def is_trustworthy(self) -> bool:
        return self.quote_verification in (QuoteVerification.EXACT, QuoteVerification.FUZZY)


@dataclass(slots=True)
class ClaimExtractionResult:
    claims: list[VerifiedClaim] = field(default_factory=list)
    dropped_unverifiable: int = 0
    batches: int = 0
    duplicates_merged: int = 0

    def metrics(self) -> dict[str, Any]:
        by_category: dict[str, int] = {}
        for item in self.claims:
            key = _category_value(item.claim.category)
            by_category[key] = by_category.get(key, 0) + 1
        return {
            "claims": len(self.claims),
            "batches": self.batches,
            "dropped_unverifiable": self.dropped_unverifiable,
            "duplicates_merged": self.duplicates_merged,
            "needs_review": sum(1 for c in self.claims if c.needs_human_review),
            "exact_quotes": sum(
                1 for c in self.claims if c.quote_verification is QuoteVerification.EXACT
            ),
            "fuzzy_quotes": sum(
                1 for c in self.claims if c.quote_verification is QuoteVerification.FUZZY
            ),
            "thesis_critical": sum(1 for c in self.claims if c.claim.is_thesis_critical),
            "by_category": by_category,
        }


class ClaimExtractionStage:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(
        self,
        pages: list[dict[str, Any]],
        *,
        max_claims: int = 60,
    ) -> ClaimExtractionResult:
        """Extract claims from composite page texts.

        ``pages`` entries need ``page_number`` and ``text`` (the composite text
        produced by the page-understanding stage), and optionally
        ``slide_title``.
        """
        usable = [p for p in pages if (p.get("text") or "").strip()]
        if not usable:
            log.warning("claims.no_text")
            return ClaimExtractionResult()

        chunks = chunk_pages(
            usable,
            max_tokens=settings.extraction_chunk_input_tokens,
            max_pages_per_chunk=settings.extraction_chunk_max_pages,
            overlap=1,
        )
        log.info(
            "claims.start",
            pages=len(usable),
            batches=len(chunks),
            max_chunk_tokens=max((c.token_estimate for c in chunks), default=0),
        )

        outcomes = await asyncio.gather(
            *(self._extract_chunk(chunk.pages, max_claims=max_claims) for chunk in chunks),
            return_exceptions=True,
        )

        raw: list[ExtractedClaim] = []
        for chunk, outcome in zip(chunks, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                log.warning(
                    "claims.batch_failed",
                    batch=chunk.index,
                    pages=chunk.page_numbers,
                    error=str(outcome)[:300],
                )
                continue
            raw.extend(outcome)

        page_text = {int(p["page_number"]): (p.get("text") or "") for p in usable}
        verified, dropped = self._verify(raw, page_text)
        deduped, merged = self._deduplicate(verified)
        deduped.sort(key=lambda c: (c.claim.is_thesis_critical, c.claim.importance), reverse=True)

        result = ClaimExtractionResult(
            claims=deduped[:max_claims],
            dropped_unverifiable=dropped,
            batches=len(chunks),
            duplicates_merged=merged,
        )
        log.info("claims.completed", **result.metrics())
        return result

    # ------------------------------------------------------------ internal ---
    async def _extract_chunk(
        self, pages: list[dict[str, Any]], *, max_claims: int
    ) -> list[ExtractedClaim]:
        user = prompts.render("claims", pages=render_pages_for_prompt(pages))
        output = await self.llm.structured(
            purpose="claims",
            stage="claims",
            system=prompts.system(),
            user=user,
            schema=ClaimExtractionOut,
            model=settings.model_reasoning,
            # A chunk of a handful of pages cannot honestly yield more than
            # this; reaching the ceiling means the response was cut off, which
            # is what turns a slow call into an unparseable one.
            max_output_tokens=settings.extraction_chunk_output_tokens,
            enforce_input_budget=True,
            context={"pages": pages, "max_claims": max_claims},
        )
        return list(output.claims)

    def _verify(
        self, claims: list[ExtractedClaim], page_text: dict[int, str]
    ) -> tuple[list[VerifiedClaim], int]:
        """Check each quote against the page it is attributed to.

        A quote that fails on its stated page is retried against the whole
        document: models occasionally attribute a correct quote to the wrong
        page, which is a provenance defect worth flagging but not a fabrication.
        """
        all_text = "\n\n".join(page_text.values())
        verified: list[VerifiedClaim] = []
        dropped = 0

        for claim in claims:
            reasons: list[str] = []
            source = page_text.get(claim.page_number, "")
            verification, score = verify_quote(claim.verbatim_quote, source)

            if verification is QuoteVerification.NOT_FOUND and all_text:
                alt_verification, alt_score = verify_quote(claim.verbatim_quote, all_text)
                if alt_verification is not QuoteVerification.NOT_FOUND:
                    verification, score = alt_verification, alt_score
                    reasons.append(
                        f"Quote was not found on the cited page {claim.page_number}; "
                        "it was located elsewhere in the document."
                    )

            if verification is QuoteVerification.NOT_FOUND:
                dropped += 1
                log.debug(
                    "claims.quote_unverified",
                    page=claim.page_number,
                    quote=collapse_whitespace(claim.verbatim_quote)[:120],
                )
                continue

            if verification is QuoteVerification.FUZZY:
                reasons.append(f"Quote matched approximately (score {score:.2f}), not verbatim.")
            if claim.confidence < LOW_CONFIDENCE:
                reasons.append(f"Low extraction confidence ({claim.confidence:.2f}).")
            if claim.from_visual:
                reasons.append("Derived from a chart or image reading rather than the text layer.")
            if lexicon.has_puffery(claim.statement):
                reasons.append("Statement contains unfalsifiable marketing language.")

            verified.append(
                VerifiedClaim(
                    claim=claim,
                    quote_verification=verification,
                    quote_match_score=score,
                    needs_human_review=bool(reasons),
                    review_reasons=reasons,
                    from_visual=claim.from_visual,
                )
            )

        if dropped:
            log.warning("claims.dropped_unverifiable", count=dropped, extracted=len(claims))
        return verified, dropped

    def _deduplicate(self, claims: list[VerifiedClaim]) -> tuple[list[VerifiedClaim], int]:
        """Merge claims restated across overlapping batches.

        The surviving instance keeps the highest importance and the strongest
        quote verification of the group.
        """
        kept: list[VerifiedClaim] = []
        keys: list[str] = []
        merged = 0

        for item in sorted(
            claims,
            key=lambda c: (c.quote_verification is QuoteVerification.EXACT, c.claim.importance),
            reverse=True,
        ):
            key = normalize_for_match(item.claim.statement)
            duplicate_index = next(
                (
                    i
                    for i, existing in enumerate(keys)
                    if fuzz.ratio(key, existing) >= DEDUPE_THRESHOLD
                ),
                None,
            )
            if duplicate_index is None:
                kept.append(item)
                keys.append(key)
                continue

            merged += 1
            winner = kept[duplicate_index]
            winner.claim.importance = max(winner.claim.importance, item.claim.importance)
            winner.claim.is_thesis_critical = (
                winner.claim.is_thesis_critical or item.claim.is_thesis_critical
            )
            for name in item.claim.entity_names:
                if name not in winner.claim.entity_names:
                    winner.claim.entity_names.append(name)

        return kept, merged


def _category_value(category: ClaimCategory | str) -> str:
    return category.value if isinstance(category, ClaimCategory) else str(category)
