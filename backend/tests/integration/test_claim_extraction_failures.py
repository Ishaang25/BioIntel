"""An extraction failure must never be reported as an empty document.

Reproduces the exact provider behaviour that broke the Moderna run: a
reasoning model given an output budget too small to think in returns
``status: incomplete`` with **no visible text at all**, because reasoning
consumed every token. Three of four claim chunks came back that way; the
fourth was cut off mid-JSON. The stage saw zero claims and told the user the
file might not be a biotech pitch deck.

Two different statements were being conflated:

    "this document contains no verifiable scientific claims"   (about the deck)
    "we never managed to read this document"                   (about us)

Only the second was ever true. These tests keep them apart.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.core.errors import LLMTruncatedError
from app.extraction.claims import ClaimExtractionFailed, ClaimExtractionStage
from app.llm.base import LLMRequest, LLMResponse
from app.llm.client import LLMClient
from app.llm.stub_provider import StubProvider

PAGES = [
    {
        "page_number": index,
        "text": (
            f"Programme MRNA-{1000 + index} is in Phase 3 efficacy testing. "
            f"The confirmed objective response rate was {20 + index}% (n={30 + index}). "
            "Treatment-related adverse events were predominantly grade 1-2."
        ),
    }
    for index in range(1, 21)
]


class StarvedProvider:
    """A reasoning model whose whole output budget went to reasoning.

    What the API actually returns in that situation: an incomplete response
    with empty ``output_text`` and ``output_tokens == max_output_tokens``.
    """

    name = "openai"

    def __init__(self, *, starve: bool = True) -> None:
        self.starve = starve
        self.calls = 0
        self._inner = StubProvider()

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.starve and request.purpose.startswith("claims"):
            budget = request.max_output_tokens or settings.llm_max_output_tokens
            raise LLMTruncatedError(
                f"Model call '{request.purpose}' produced no visible output: all {budget} "
                "output tokens went to reasoning before the answer began.",
                detail={"output_tokens": budget, "reasoning_tokens": budget},
            )
        return await self._inner.complete_structured(request)

    async def embed(self, texts, *, model, dimensions):
        return await self._inner.embed(texts, model=model, dimensions=dimensions)

    async def aclose(self) -> None:
        await self._inner.aclose()


class PartiallyStarvedProvider(StarvedProvider):
    """Only the pages in ``doomed`` cannot be read."""

    def __init__(self, doomed: set[int]) -> None:
        super().__init__(starve=False)
        self.doomed = doomed

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        if request.purpose.startswith("claims") and any(
            f"=== PAGE {page} " in request.user or f"=== PAGE {page}\n" in request.user
            for page in self.doomed
        ):
            self.calls += 1
            raise LLMTruncatedError("no visible output", detail={"output_tokens": 6000})
        return await super().complete_structured(request)


class TestTotalFailureIsNotAnEmptyDocument:
    async def test_losing_every_chunk_raises_an_extraction_error(self):
        llm = LLMClient(StarvedProvider(), persist_logs=False)

        with pytest.raises(ClaimExtractionFailed) as caught:
            await ClaimExtractionStage(llm).run(PAGES)

        message = str(caught.value)
        assert "never read" in message
        assert "not an empty document" in message

    async def test_the_error_does_not_blame_the_document(self):
        llm = LLMClient(StarvedProvider(), persist_logs=False)

        with pytest.raises(ClaimExtractionFailed) as caught:
            await ClaimExtractionStage(llm).run(PAGES)

        # The sentence that sent the investigation in the wrong direction.
        assert "may not be a biotech pitch deck" not in str(caught.value)

    async def test_the_failure_carries_a_rejection_audit(self):
        llm = LLMClient(StarvedProvider(), persist_logs=False)

        with pytest.raises(ClaimExtractionFailed) as caught:
            await ClaimExtractionStage(llm).run(PAGES)

        rejections = caught.value.detail["rejections"]
        assert rejections["by_reason"]["chunk_extraction_failed"] > 0
        assert rejections["examples"], "an audit with no examples explains nothing"


class TestPartialFailureDegrades:
    async def test_surviving_chunks_still_produce_claims(self):
        provider = PartiallyStarvedProvider(doomed={1, 2, 3, 4})
        llm = LLMClient(provider, persist_logs=False)

        result = await ClaimExtractionStage(llm).run(PAGES)

        assert result.claims, "one bad chunk must not cost the whole document"
        assert result.extraction_succeeded

    async def test_the_lost_pages_are_named_not_hidden(self):
        """A half-recovered chunk must not pass for a whole one."""
        doomed = {1, 2, 3, 4}
        provider = PartiallyStarvedProvider(doomed=doomed)
        llm = LLMClient(provider, persist_logs=False)

        result = await ClaimExtractionStage(llm).run(PAGES)

        assert doomed.issubset(set(result.pages_not_read)), (
            f"pages {sorted(doomed)} were never read but the stage reported {result.pages_not_read}"
        )
        assert result.audit.reasons["pages_not_read_after_retry"] >= 1

    async def test_no_claim_is_attributed_to_a_page_that_was_never_read(self):
        provider = PartiallyStarvedProvider(doomed={1, 2, 3, 4})
        llm = LLMClient(provider, persist_logs=False)

        result = await ClaimExtractionStage(llm).run(PAGES)

        unread = set(result.pages_not_read)
        assert not [c for c in result.claims if c.claim.page_number in unread]


class TestRejectionAudit:
    async def test_every_surviving_claim_is_accounted_for(self):
        llm = LLMClient(StubProvider(), persist_logs=False)

        result = await ClaimExtractionStage(llm).run(PAGES)

        # Candidates either survive, or appear in the audit with a reason.
        assert result.candidates >= len(result.claims)
        assert result.candidates - len(result.claims) == result.audit.total

    async def test_dropped_quotes_name_the_page_and_the_score(self):
        """A quote rejection must be checkable against the document by hand."""
        llm = LLMClient(StubProvider(), persist_logs=False)
        pages = [{"page_number": 1, "text": "MRNA-1010 is in Phase 3 efficacy testing."}]

        result = await ClaimExtractionStage(llm).run(pages)

        for example in result.audit.examples:
            assert example["reason"]
            if example["reason"] == "quote_not_found_in_document":
                assert example["page"] is not None
                assert "match_score" in example

    async def test_audit_samples_are_bounded(self):
        """The audit rides along in run metrics; it must not grow without limit."""
        llm = LLMClient(StubProvider(), persist_logs=False)

        result = await ClaimExtractionStage(llm).run(PAGES * 5)

        assert len(result.audit.examples) <= result.audit.sample_limit


class TestClaimChunksRetryAlone:
    async def test_a_failing_chunk_is_split_before_it_is_abandoned(self):
        provider = PartiallyStarvedProvider(doomed={5})
        llm = LLMClient(provider, persist_logs=False)

        result = await ClaimExtractionStage(llm).run(PAGES)

        assert result.batch_retries > 0, "the chunk was dropped without being retried"
        assert result.claims

    async def test_retries_never_resend_the_whole_document(self):
        provider = PartiallyStarvedProvider(doomed={5})
        llm = LLMClient(provider, persist_logs=False)
        await ClaimExtractionStage(llm).run(PAGES)

        for request in [r for r in _requests(provider) if r.purpose.startswith("claims")]:
            pages = sum(1 for page in PAGES if f"=== PAGE {page['page_number']} " in request.user)
            assert pages <= settings.extraction_chunk_max_pages


def _requests(provider) -> list[LLMRequest]:
    return getattr(provider, "requests", [])


class TestBudgetIsSentWithHeadroom:
    async def test_claim_calls_ask_for_more_than_the_answer_needs(self):
        """The fix, observed at the wire rather than in a helper."""
        seen: list[int] = []

        class Recording:
            name = "openai"

            def __init__(self) -> None:
                self._inner = StubProvider()

            async def complete_structured(self, request: LLMRequest) -> LLMResponse:
                if request.purpose.startswith("claims"):
                    seen.append(request.max_output_tokens or 0)
                return await self._inner.complete_structured(request)

            async def embed(self, texts, *, model, dimensions):
                return []

            async def aclose(self) -> None:
                return None

        llm = LLMClient(Recording(), persist_logs=False)
        await ClaimExtractionStage(llm).run(PAGES)

        assert seen
        for budget in seen:
            assert budget > settings.extraction_chunk_output_tokens, (
                "the answer is sharing its budget with the model's reasoning again"
            )


def test_truncation_error_is_not_a_generic_llm_error():
    """The provider must name the cause; the caller's remedy depends on it."""
    from app.core.errors import LLMError, LLMSchemaError

    error = LLMTruncatedError("cut off")
    assert isinstance(error, LLMError)
    assert isinstance(error, LLMSchemaError)
    assert error.code == "llm_truncated"
    assert json.dumps(error.to_payload())
