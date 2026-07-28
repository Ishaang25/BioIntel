"""Performance regression for the BioNTech corporate deck.

The failure this module locks down, measured on a real 25-page BioNTech
presentation:

    stage      entities      FAILED    719,618 ms
    Response was not valid JSON: Expecting ',' delimiter: line 1 column 13324

    llm call   entities        542,709 ms   21,871 in   16,000 out
    llm call   entities:repair 176,894 ms   22,906 in   16,000 out

Every number there is a symptom of one decision: the whole presentation went
to a single model call.  The input was over the 20k per-call budget, the entity
list it asked for could not fit in the 16,000-token output ceiling, the reply
came back cut in half, and the repair path resent *the same oversized prompt*
and truncated at the same place -- twelve minutes to produce nothing.

These tests assert the shape of the fix rather than a wall-clock number on a
particular machine:

1. the deck is split into chunks and no call approaches the input budget;
2. chunks are extracted concurrently, not one after another;
3. a failing chunk is retried alone, and the other chunks' work survives;
4. a truncated response is salvaged instead of discarded;
5. the pipeline records per-stage timings so the next regression is visible.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from app.core.config import settings
from app.extraction.entities import EntityExtractionStage
from app.llm.base import LLMRequest, LLMResponse, Usage
from app.llm.client import LLMClient, estimated_input_tokens
from app.llm.stub_provider import StubProvider
from app.utils.chunking import PROMPT_OVERHEAD_TOKENS, chunk_pages
from app.utils.text import token_estimate
from tests.fixtures.biontech_deck import biontech_pages

#: The ceiling requirement: no single call may exceed roughly this.
INPUT_TOKEN_CEILING = 20_000


def composite_pages() -> list[dict[str, Any]]:
    """The page view the extraction stages actually consume."""
    return [
        {
            "page_number": number,
            "page_id": f"p{number}",
            "slide_title": text.splitlines()[0][:80],
            "text": text,
        }
        for number, text in enumerate(biontech_pages(), start=1)
    ]


# --------------------------------------------------------------- providers ---
class RecordingProvider:
    """Stub provider that records every request and how they overlapped."""

    name = "stub"

    def __init__(self, *, delay: float = 0.0) -> None:
        self._inner = StubProvider()
        self._delay = delay
        self.requests: list[LLMRequest] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            return await self._inner.complete_structured(request)
        finally:
            self.in_flight -= 1

    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        return await self._inner.embed(texts, model=model, dimensions=dimensions)

    async def aclose(self) -> None:
        await self._inner.aclose()

    def entity_requests(self) -> list[LLMRequest]:
        return [r for r in self.requests if r.purpose.startswith("entities")]


class LatencyOnlyProvider:
    """Simulates provider latency with no work of its own.

    The offline stub does real lexical analysis on the calling thread, which
    serialises and would mask the concurrency this test is measuring. Network
    latency is what actually dominates a live run, so that is what is modelled.
    """

    name = "stub"

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self.requests: list[LLMRequest] = []

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        await asyncio.sleep(self._delay)
        return LLMResponse(
            text=json.dumps({"entities": []}),
            model=f"stub/{request.model}",
            usage=Usage(input_tokens=token_estimate(request.user), output_tokens=8),
            latency_ms=int(self._delay * 1000),
            provider=self.name,
        )

    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def aclose(self) -> None:
        return None


class FlakyProvider(RecordingProvider):
    """Fails requests covering ``poison_pages``.

    Models the real failure mode: one chunk's answer does not come back
    usable, and the question is whether the pipeline re-sends that chunk or
    the whole deck.
    """

    def __init__(self, poison_pages: set[int], *, permanent: bool = False) -> None:
        super().__init__()
        self.poison_pages = poison_pages
        self.permanent = permanent
        self.failures = 0

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        poisoned = request.purpose.startswith("entities") and any(
            f"=== PAGE {page} " in request.user or f"=== PAGE {page}\n" in request.user
            for page in self.poison_pages
        )
        if poisoned and (self.permanent or self.failures == 0):
            self.requests.append(request)
            self.failures += 1
            raise RuntimeError("simulated provider failure")
        return await super().complete_structured(request)


class TruncatingProvider(RecordingProvider):
    """Returns entity JSON cut off mid-object, flagged incomplete.

    This is byte-for-byte the situation that produced
    ``Expecting ',' delimiter``.
    """

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        response = await super().complete_structured(request)
        if not request.purpose.startswith("entities"):
            return response
        payload = json.loads(response.text)
        if len(payload.get("entities", [])) < 2:
            return response
        cut = response.text.rfind("},") + 2
        return LLMResponse(
            text=response.text[: cut + 30],  # mid-way through the next object
            model=response.model,
            usage=Usage(input_tokens=response.usage.input_tokens, output_tokens=6_000),
            latency_ms=response.latency_ms,
            provider=self.name,
            truncated=True,
        )


# ================================================== the regression itself ===
class TestTheDeckIsNeverSentAsOneCall:
    def test_the_whole_deck_would_exceed_the_per_call_budget(self):
        """Establishes that the fixture reproduces the condition that failed."""
        pages = composite_pages()
        whole_deck = sum(token_estimate(p["text"]) for p in pages) + PROMPT_OVERHEAD_TOKENS
        assert whole_deck > INPUT_TOKEN_CEILING, (
            f"the fixture is only {whole_deck} tokens; it no longer reproduces the "
            "oversized single-call condition this test exists to prevent"
        )

    def test_the_deck_is_split_into_several_chunks(self):
        chunks = chunk_pages(
            composite_pages(),
            max_tokens=settings.extraction_chunk_input_tokens,
            max_pages_per_chunk=settings.extraction_chunk_max_pages,
            overlap=0,
        )
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.pages) <= settings.extraction_chunk_max_pages
            assert chunk.token_estimate + PROMPT_OVERHEAD_TOKENS < INPUT_TOKEN_CEILING

    async def test_no_entity_call_exceeds_the_input_budget(self):
        provider = RecordingProvider()
        llm = LLMClient(provider, persist_logs=False)
        await EntityExtractionStage(llm).run(composite_pages())

        calls = provider.entity_requests()
        assert len(calls) > 1, "the deck must not go to a single call"
        for request in calls:
            assert estimated_input_tokens(request) <= INPUT_TOKEN_CEILING

    async def test_every_page_reaches_some_chunk(self):
        """Chunking must not lose pages -- the cheapest way to look fast."""
        provider = RecordingProvider()
        llm = LLMClient(provider, persist_logs=False)
        await EntityExtractionStage(llm).run(composite_pages())

        covered = {
            number
            for request in provider.entity_requests()
            for number in range(1, len(composite_pages()) + 1)
            if f"=== PAGE {number}" in request.user
        }
        assert covered == set(range(1, len(composite_pages()) + 1))


class TestChunksRunInParallel:
    async def test_chunks_are_in_flight_together(self):
        provider = RecordingProvider(delay=0.05)
        llm = LLMClient(provider, persist_logs=False)
        await EntityExtractionStage(llm).run(composite_pages())
        assert provider.max_in_flight > 1, "chunks were processed sequentially"

    async def test_wall_clock_tracks_the_slowest_chunk_not_their_sum(self):
        """The 30-second goal is only reachable if latency overlaps.

        With per-call latency L and N chunks, the serial design costs N*L --
        which is how one deck became twelve minutes. Concurrent extraction
        must cost roughly L.
        """
        pages = composite_pages()
        delay = 0.3

        async def measure(latency: float) -> tuple[float, int]:
            llm = LLMClient(LatencyOnlyProvider(latency), persist_logs=False)
            started = time.perf_counter()
            result = await EntityExtractionStage(llm).run(pages)
            return time.perf_counter() - started, result.chunks

        # Best of two passes each. The stage does real deterministic work of
        # its own (gazetteer backstop, salience scoring), so the model wait is
        # the difference between the two measurements -- and on a loaded CI
        # machine a single sample of either can be inflated by the scheduler,
        # which would fail this test for a reason that has nothing to do with
        # concurrency. Taking the best sample measures the code, not the load.
        baselines: list[float] = []
        for _ in range(2):
            duration, _chunks = await measure(0.0)
            baselines.append(duration)
        baseline = min(baselines)

        timings: list[float] = []
        chunks = 0
        for _ in range(2):
            duration, chunks = await measure(delay)
            timings.append(duration)
        elapsed = min(timings)

        assert chunks > 1
        model_time = elapsed - baseline
        sequential = delay * chunks
        assert model_time < sequential * 0.5, (
            f"{chunks} chunks spent {model_time:.2f}s waiting on the model; "
            f"sequential would be {sequential:.2f}s"
        )


class TestOnlyTheFailedChunkIsRetried:
    async def test_retry_covers_the_failed_pages_only(self):
        poison = 9
        provider = FlakyProvider(poison_pages={poison})
        llm = LLMClient(provider, persist_logs=False)
        result = await EntityExtractionStage(llm).run(composite_pages())

        assert provider.failures == 1
        assert result.chunk_retries > 0
        assert result.chunk_failures == 0, "the chunk should have recovered after splitting"

        # The retries went out after the failure. None of them may carry the
        # whole deck: that is the twelve-minute behaviour being prevented.
        retries = provider.entity_requests()[-result.chunk_retries :]
        failed_chunk_pages = _pages_in(provider.requests[1])
        for request in retries:
            assert estimated_input_tokens(request) <= INPUT_TOKEN_CEILING
            assert _pages_in(request) <= failed_chunk_pages, (
                "the retry pulled in pages outside the chunk that failed"
            )
        assert any(poison in _pages_in(request) for request in retries)

    async def test_a_permanently_failing_chunk_does_not_lose_the_others(self):
        """One unrecoverable chunk degrades that chunk, not the stage."""
        pages = composite_pages()
        doomed = set(range(9, 17))  # a whole chunk, so both halves also fail
        provider = FlakyProvider(poison_pages=doomed, permanent=True)
        llm = LLMClient(provider, persist_logs=False)
        result = await EntityExtractionStage(llm).run(pages)

        assert result.chunk_failures >= 1
        assert result.entities, "the surviving chunks' entities must still be returned"
        covered = {page for entity in result.entities for page in entity.source_pages}
        assert covered - doomed, "no pages outside the failed chunk contributed entities"

    async def test_pages_lost_in_a_half_recovered_chunk_are_reported(self):
        """A split retry that half-succeeds must not report a clean chunk.

        Otherwise pages vanish from the analysis with nothing recording it --
        the stage says "7 chunks, 0 failures" while four pages were never read.
        """
        doomed = {9, 10, 11, 12}
        provider = FlakyProvider(poison_pages=doomed, permanent=True)
        llm = LLMClient(provider, persist_logs=False)
        result = await EntityExtractionStage(llm).run(composite_pages())

        assert doomed.issubset(set(result.pages_not_read)), (
            f"pages {sorted(doomed)} were never read but the stage reported {result.pages_not_read}"
        )
        assert result.entities, "the readable half of the chunk must still contribute"


class TestTruncatedOutputIsSalvaged:
    async def test_a_cut_off_response_yields_partial_entities(self):
        provider = TruncatingProvider()
        llm = LLMClient(provider, persist_logs=False)
        result = await EntityExtractionStage(llm).run(composite_pages())

        assert llm.metrics.salvaged > 0, "truncated output was not salvaged"
        assert result.entities, "a truncated response must degrade, not fail"

    async def test_truncation_never_triggers_a_whole_prompt_repair(self):
        """Repairing a truncated answer just truncates again, and doubles the bill."""
        provider = TruncatingProvider()
        llm = LLMClient(provider, persist_logs=False)
        await EntityExtractionStage(llm).run(composite_pages())

        assert not [r for r in provider.requests if r.purpose.endswith(":repair")]
        assert llm.metrics.repairs == 0


class TestExtractionIsInstrumented:
    async def test_stage_reports_chunking_metrics(self):
        llm = LLMClient(RecordingProvider(), persist_logs=False)
        metrics = (await EntityExtractionStage(llm).run(composite_pages())).metrics()

        assert metrics["chunks"] > 1
        assert metrics["max_chunk_input_tokens"] > 0
        assert "chunk_failures" in metrics
        assert "duplicates_merged" in metrics

    async def test_every_call_records_tokens_latency_and_cost(self):
        llm = LLMClient(RecordingProvider(delay=0.01), persist_logs=False)
        await EntityExtractionStage(llm).run(composite_pages())

        stage = llm.metrics.by_stage["entities"].to_dict()
        assert stage["calls"] > 1
        assert stage["input_tokens"] > 0
        assert stage["output_tokens"] > 0
        assert stage["latency_ms_total"] > 0
        assert stage["input_tokens_max"] <= INPUT_TOKEN_CEILING
        assert "estimated_cost_usd" in stage
        assert "provider_retries" in stage


class TestEntitiesSurviveChunking:
    """Chunking must not degrade what the stage produces."""

    async def test_programmes_named_across_the_deck_are_found_once(self):
        llm = LLMClient(RecordingProvider(), persist_logs=False)
        result = await EntityExtractionStage(llm).run(composite_pages())

        keys = [e.normalized_key for e in result.entities]
        assert len(keys) == len(set(keys)), "the same entity survived as several rows"

    async def test_entities_carry_pages_from_more_than_one_chunk(self):
        """Proof that cross-chunk merging actually merged something."""
        llm = LLMClient(RecordingProvider(), persist_logs=False)
        result = await EntityExtractionStage(llm).run(composite_pages())

        spread = max((len(e.source_pages) for e in result.entities), default=0)
        assert spread > settings.extraction_chunk_max_pages, (
            "no entity spans more than one chunk; merging did not happen"
        )


def _pages_in(request: LLMRequest) -> set[int]:
    return {
        number
        for number in range(1, 200)
        if f"=== PAGE {number} " in request.user or f"=== PAGE {number}\n" in request.user
    }


@pytest.fixture
def biontech_run(settings, monkeypatch) -> str:
    from app.db.models import AnalysisRun
    from app.db.session import session_scope
    from app.services.documents import store_document
    from tests.fixtures.biontech_deck import biontech_pdf

    monkeypatch.setattr(settings, "retrieval_enabled", False)
    monkeypatch.setattr(settings, "regulatory_verification_enabled", False)

    with session_scope() as session:
        document, _ = store_document(
            session, data=biontech_pdf(), filename="biontech-corporate.pdf"
        )
        session.flush()
        run = AnalysisRun(document_id=document.id)
        session.add(run)
        session.flush()
        return run.id


class TestPipelineProfile:
    async def test_run_records_a_timing_for_every_stage(self, biontech_run):
        from app.core.enums import STAGE_ORDER
        from app.db.models import AnalysisRun
        from app.db.session import session_scope
        from app.pipeline.orchestrator import AnalysisPipeline

        llm = LLMClient(RecordingProvider(), run_id=biontech_run, persist_logs=False)
        context = await AnalysisPipeline(llm=llm).run(biontech_run)

        assert len(context.stage_timings) == len(STAGE_ORDER)
        for timing in context.stage_timings:
            assert timing["duration_ms"] >= 0
            assert "llm" in timing

        with session_scope() as session:
            run = session.get(AnalysisRun, biontech_run)
        assert run.metrics["timings"], "stage timings must be persisted with the run"
        assert "by_stage" in run.metrics["llm"]

    async def test_no_call_in_the_whole_run_exceeds_the_input_budget(self, biontech_run):
        from app.pipeline.orchestrator import AnalysisPipeline

        provider = RecordingProvider()
        llm = LLMClient(provider, run_id=biontech_run, persist_logs=False)
        await AnalysisPipeline(llm=llm).run(biontech_run)

        oversized = [
            (r.purpose, estimated_input_tokens(r))
            for r in provider.requests
            if estimated_input_tokens(r) > INPUT_TOKEN_CEILING
        ]
        assert not oversized, f"calls over the {INPUT_TOKEN_CEILING}-token budget: {oversized}"
        assert llm.metrics.over_input_budget == 0
