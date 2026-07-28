"""Stage 5: scientific claim extraction with verified provenance.

The contract this module enforces is what makes the rest of the product
trustworthy: **every claim carries a quote that provably appears in the
document.**  A claim whose quote cannot be located is not silently kept -- it
is either downgraded and flagged for human review, or dropped.

Pipeline within the stage:

    page batches -> parallel extraction -> quote verification
                 -> cross-batch deduplication -> importance re-ranking

Every candidate that does not survive that path is recorded with the reason
(:class:`RejectionAudit`).  That exists because of a real incident: an
undersized output budget starved the model's reasoning, three of four chunks
came back empty, and the stage reported "no verifiable scientific claims"
about a deck full of Phase 3 readouts.  Zero claims and zero explanation is
indistinguishable from a document with nothing in it, which sent the
investigation in the wrong direction entirely.  A stage that discards work now
has to say what it discarded and why.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.core.config import settings
from app.core.enums import ClaimCategory, QuoteVerification
from app.core.errors import BioIntelError
from app.core.logging import get_logger
from app.extraction import lexicon
from app.llm import prompts
from app.llm.budgets import output_budget
from app.llm.client import LLMClient
from app.llm.schemas import ClaimExtractionOut, ExtractedClaim
from app.utils.chunking import PageChunk, chunk_pages, render_pages_for_prompt
from app.utils.text import collapse_whitespace, normalize_for_match, truncate, verify_quote

log = get_logger(__name__)

#: Two claims whose statements match above this are treated as one.
DEDUPE_THRESHOLD = 88.0
#: Claims below this extraction confidence are flagged rather than trusted.
LOW_CONFIDENCE = 0.45


class ClaimExtractionFailed(BioIntelError):
    """Every chunk failed. The document was never actually read.

    Deliberately distinct from "this document contains no claims": one is a
    fact about the deck, the other is a fact about our infrastructure, and
    telling a user the first when the second is true is how a 25-page
    investor presentation gets described as not a biotech pitch deck.
    """

    code = "claim_extraction_failed"
    status_code = 502
    message = "Claim extraction could not be completed."


@dataclass(slots=True)
class ChunkOutcome:
    """What one chunk produced, including what it failed to produce.

    ``pages_lost`` exists because a split retry can *partly* succeed: pages
    1-4 stay unreadable while pages 5-8 come back fine. Counting that as a
    clean chunk hides the loss -- the stage reports success and four pages of
    the deck are simply missing from the analysis with nothing to show for it.
    """

    claims: list[ExtractedClaim] = field(default_factory=list)
    retries: int = 0
    pages_lost: list[int] = field(default_factory=list)


@dataclass(slots=True)
class RejectionAudit:
    """Why each candidate claim did not make it into the report."""

    #: reason -> count
    reasons: Counter[str] = field(default_factory=Counter)
    #: A bounded sample, for the log and for a human reading stage metrics.
    examples: list[dict[str, Any]] = field(default_factory=list)
    sample_limit: int = 40

    def record(
        self, reason: str, *, statement: str = "", page: int | None = None, **extra: Any
    ) -> None:
        self.reasons[reason] += 1
        if len(self.examples) < self.sample_limit:
            self.examples.append(
                {
                    "reason": reason,
                    "statement": truncate(collapse_whitespace(statement), 160),
                    "page": page,
                    **extra,
                }
            )

    @property
    def total(self) -> int:
        return sum(self.reasons.values())

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "by_reason": dict(self.reasons), "examples": self.examples}


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
    #: Chunks whose extraction never produced output, even after splitting.
    batch_failures: int = 0
    batch_retries: int = 0
    #: Raw candidates the model returned, before any filtering.
    candidates: int = 0
    #: Pages the model never successfully read. Any claim on them is missing
    #: from this analysis, which the report has to disclose.
    pages_not_read: list[int] = field(default_factory=list)
    audit: RejectionAudit = field(default_factory=RejectionAudit)

    @property
    def extraction_succeeded(self) -> bool:
        """True when at least one chunk was actually read by the model."""
        return self.batches == 0 or self.batch_failures < self.batches

    def metrics(self) -> dict[str, Any]:
        by_category: dict[str, int] = {}
        for item in self.claims:
            key = _category_value(item.claim.category)
            by_category[key] = by_category.get(key, 0) + 1
        return {
            "claims": len(self.claims),
            "batches": self.batches,
            "batch_failures": self.batch_failures,
            "batch_retries": self.batch_retries,
            "candidates": self.candidates,
            "pages_not_read": self.pages_not_read,
            "dropped_unverifiable": self.dropped_unverifiable,
            "duplicates_merged": self.duplicates_merged,
            "rejections": self.audit.to_dict(),
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

        audit = RejectionAudit()
        outcomes = await asyncio.gather(
            *(self._extract_chunk(chunk, max_claims=max_claims) for chunk in chunks),
            return_exceptions=True,
        )

        raw: list[ExtractedClaim] = []
        failures = 0
        retries = 0
        pages_lost: list[int] = []
        for chunk, outcome in zip(chunks, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                failures += 1
                pages_lost.extend(chunk.page_numbers)
                audit.record(
                    "chunk_extraction_failed",
                    statement=f"pages {chunk.page_numbers}",
                    pages=chunk.page_numbers,
                    error=str(outcome)[:200],
                )
                log.warning(
                    "claims.batch_failed",
                    batch=chunk.index,
                    pages=chunk.page_numbers,
                    error=str(outcome)[:300],
                )
                continue
            retries += outcome.retries
            raw.extend(outcome.claims)
            if outcome.pages_lost:
                # Partial recovery: some pages of this chunk were never read.
                pages_lost.extend(outcome.pages_lost)
                audit.record(
                    "pages_not_read_after_retry",
                    statement=f"pages {outcome.pages_lost}",
                    pages=outcome.pages_lost,
                )
                log.warning(
                    "claims.batch_partially_lost",
                    batch=chunk.index,
                    pages=outcome.pages_lost,
                )

        if chunks and failures == len(chunks):
            # Nothing was read. Reporting "no claims found" here would be a
            # statement about the document that we have no basis to make.
            log.error("claims.all_batches_failed", batches=len(chunks))
            raise ClaimExtractionFailed(
                f"All {len(chunks)} claim-extraction batches failed, so the document was "
                "never read. This is an extraction failure, not an empty document.",
                detail={"batches": len(chunks), "rejections": audit.to_dict()},
            )

        page_text = {int(p["page_number"]): (p.get("text") or "") for p in usable}
        verified, dropped = self._verify(raw, page_text, audit)
        deduped, merged = self._deduplicate(verified, audit)
        deduped.sort(key=lambda c: (c.claim.is_thesis_critical, c.claim.importance), reverse=True)

        for surplus in deduped[max_claims:]:
            audit.record(
                "over_max_claims",
                statement=surplus.claim.statement,
                page=surplus.claim.page_number,
                importance=surplus.claim.importance,
            )

        result = ClaimExtractionResult(
            claims=deduped[:max_claims],
            dropped_unverifiable=dropped,
            batches=len(chunks),
            batch_failures=failures,
            batch_retries=retries,
            candidates=len(raw),
            duplicates_merged=merged,
            pages_not_read=sorted(set(pages_lost)),
            audit=audit,
        )
        log.info("claims.completed", **result.metrics())
        return result

    # ------------------------------------------------------------ internal ---
    async def _extract_chunk(self, chunk: PageChunk, *, max_claims: int) -> ChunkOutcome:
        """Extract one chunk, splitting and retrying it alone if it fails.

        Mirrors the entity stage: only the chunk that failed is re-sent, never
        the whole document.  A chunk that exhausted its output budget is
        halved, which both shortens the answer and leaves more of the budget
        for the reasoning that precedes it.

        A half that still fails is reported in ``pages_lost`` rather than
        quietly dropped, so a partial recovery cannot pass for a whole one.
        """
        try:
            return ChunkOutcome(claims=list(await self._call(chunk, max_claims=max_claims)))
        except Exception as exc:
            halves = chunk.split()
            if not halves:
                raise
            log.warning(
                "claims.batch_retrying_split",
                batch=chunk.index,
                pages=chunk.page_numbers,
                error=str(exc)[:200],
            )

        outcome = ChunkOutcome()
        results = await asyncio.gather(
            *(self._call(half, max_claims=max_claims) for half in halves),
            return_exceptions=True,
        )
        for half, result in zip(halves, results, strict=True):
            outcome.retries += 1
            if isinstance(result, BaseException):
                outcome.pages_lost.extend(half.page_numbers)
                log.warning(
                    "claims.batch_retry_failed",
                    batch=chunk.index,
                    pages=half.page_numbers,
                    error=str(result)[:200],
                )
                continue
            outcome.claims.extend(result)
        if not outcome.claims:
            raise ClaimExtractionFailed(
                f"Claim extraction failed for pages {chunk.page_numbers} after splitting."
            )
        return outcome

    async def _call(self, chunk: PageChunk, *, max_claims: int) -> list[ExtractedClaim]:
        user = prompts.render("claims", pages=render_pages_for_prompt(chunk.pages))
        output = await self.llm.structured(
            purpose="claims",
            stage="claims",
            system=prompts.system(),
            user=user,
            schema=ClaimExtractionOut,
            model=settings.model_reasoning,
            # Room for a chunk's claims *and* for the reasoning the model does
            # first. Budgeting only for the answer starved gpt-5 so completely
            # that it returned nothing at all. See app.llm.budgets.
            max_output_tokens=output_budget(
                settings.model_reasoning,
                content_tokens=settings.extraction_chunk_output_tokens,
                effort=settings.llm_reasoning_effort,
            ),
            enforce_input_budget=True,
            context={"pages": chunk.pages, "max_claims": max_claims},
        )
        return list(output.claims)

    def _verify(
        self, claims: list[ExtractedClaim], page_text: dict[int, str], audit: RejectionAudit
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
                audit.record(
                    "quote_not_found_in_document",
                    statement=claim.statement,
                    page=claim.page_number,
                    quote=truncate(collapse_whitespace(claim.verbatim_quote), 120),
                    match_score=round(score, 3),
                    page_had_text=bool(source.strip()),
                )
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

    def _deduplicate(
        self, claims: list[VerifiedClaim], audit: RejectionAudit
    ) -> tuple[list[VerifiedClaim], int]:
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
            audit.record(
                "duplicate_of_earlier_claim",
                statement=item.claim.statement,
                page=item.claim.page_number,
                merged_into=truncate(collapse_whitespace(winner.claim.statement), 120),
            )
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
