"""Evidence retrieval orchestration.

Turns a per-claim query plan into a ranked, deduplicated evidence set:

    query plan -> parallel source fan-out -> cross-source dedupe
               -> relevance scoring (semantic + lexical) -> quality ranking

Ranking runs *before* adjudication because adjudication is the expensive step:
we want the model reading the eight records most likely to settle the claim,
not the first eight PubMed returned.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.core.config import settings
from app.core.enums import PUBLICATION_TYPE_WEIGHT, EvidenceSource
from app.core.errors import RetrievalError
from app.core.logging import get_logger
from app.evidence.clinicaltrials import ClinicalTrialsClient
from app.evidence.europepmc import EuropePMCClient
from app.evidence.models import EvidenceRecord
from app.evidence.pubmed import PubMedClient
from app.llm.client import LLMClient
from app.utils.text import normalize_for_match

log = get_logger(__name__)


@dataclass(slots=True)
class RetrievalRequest:
    """What to search for on behalf of one claim."""

    claim_id: str
    claim_statement: str
    pubmed_queries: list[str] = field(default_factory=list)
    trial_conditions: list[str] = field(default_factory=list)
    trial_interventions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScoredRecord:
    record: EvidenceRecord
    relevance: float
    quality: float
    rank_score: float
    similarity: float = 0.0


@dataclass(slots=True)
class RetrievalResult:
    claim_id: str
    records: list[ScoredRecord] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_before_ranking: int = 0


class EvidenceRetriever:
    """Fan-out retrieval across literature and registry sources."""

    def __init__(
        self,
        *,
        pubmed: PubMedClient | None = None,
        europepmc: EuropePMCClient | None = None,
        clinicaltrials: ClinicalTrialsClient | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.pubmed = pubmed or PubMedClient()
        self.europepmc = europepmc or EuropePMCClient()
        self.clinicaltrials = clinicaltrials or ClinicalTrialsClient()
        self.llm = llm
        self._semaphore = asyncio.Semaphore(settings.retrieval_concurrency)
        self.stats: dict[str, int] = {}

    # ----------------------------------------------------------- retrieval ---
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        result = RetrievalResult(claim_id=request.claim_id)
        if not settings.retrieval_enabled:
            return result

        tasks: list[asyncio.Task[list[EvidenceRecord]]] = []
        for query in request.pubmed_queries[:4]:
            tasks.append(asyncio.create_task(self._search_pubmed(query)))
            tasks.append(asyncio.create_task(self._search_europepmc(query)))
            result.queries_run.append(query)
        if request.trial_conditions or request.trial_interventions:
            tasks.append(
                asyncio.create_task(
                    self._search_trials(request.trial_conditions, request.trial_interventions)
                )
            )
            result.queries_run.append(
                f"ctgov(cond={request.trial_conditions}, intr={request.trial_interventions})"
            )

        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        records: list[EvidenceRecord] = []
        for outcome in gathered:
            if isinstance(outcome, BaseException):
                message = f"{type(outcome).__name__}: {outcome}"
                result.errors.append(message[:300])
                log.warning("retrieval.source_failed", error=message[:300])
                continue
            records.extend(outcome)

        deduped = deduplicate(records)
        result.total_before_ranking = len(deduped)
        result.records = await self.rank(request.claim_statement, deduped)
        log.info(
            "retrieval.completed",
            claim_id=request.claim_id,
            raw=len(records),
            deduped=len(deduped),
            kept=len(result.records),
            errors=len(result.errors),
        )
        return result

    async def retrieve_many(self, requests: list[RetrievalRequest]) -> list[RetrievalResult]:
        async def bounded(req: RetrievalRequest) -> RetrievalResult:
            async with self._semaphore:
                try:
                    return await self.retrieve(req)
                except RetrievalError as exc:
                    log.warning("retrieval.claim_failed", claim_id=req.claim_id, error=str(exc))
                    return RetrievalResult(claim_id=req.claim_id, errors=[str(exc)[:300]])

        return list(await asyncio.gather(*(bounded(r) for r in requests)))

    async def _search_pubmed(self, query: str) -> list[EvidenceRecord]:
        records = await self.pubmed.search(query)
        self._bump("pubmed", len(records))
        return records

    async def _search_europepmc(self, query: str) -> list[EvidenceRecord]:
        records = await self.europepmc.search(query)
        self._bump("europe_pmc", len(records))
        return records

    async def _search_trials(
        self, conditions: list[str], interventions: list[str]
    ) -> list[EvidenceRecord]:
        records = await self.clinicaltrials.search(
            conditions=conditions, interventions=interventions
        )
        self._bump("clinicaltrials_gov", len(records))
        return records

    def _bump(self, key: str, count: int) -> None:
        self.stats[key] = self.stats.get(key, 0) + count

    # ------------------------------------------------------------- ranking ---
    async def rank(
        self, claim_statement: str, records: list[EvidenceRecord], *, limit: int | None = None
    ) -> list[ScoredRecord]:
        if not records:
            return []
        limit = limit or settings.evidence_per_claim

        similarities = await self._semantic_similarity(claim_statement, records)
        scored: list[ScoredRecord] = []
        for record, similarity in zip(records, similarities, strict=True):
            lexical = lexical_relevance(claim_statement, record)
            # Semantic similarity is the better signal when available; lexical
            # overlap keeps rare gene symbols and asset codes from being lost.
            relevance = round(0.6 * similarity + 0.4 * lexical, 4) if similarity else lexical
            quality = quality_score(record)
            scored.append(
                ScoredRecord(
                    record=record,
                    relevance=relevance,
                    quality=quality,
                    similarity=round(similarity, 4),
                    rank_score=round(0.65 * relevance + 0.35 * quality, 4),
                )
            )

        scored.sort(key=lambda s: s.rank_score, reverse=True)
        return diversify(scored, limit)

    async def _semantic_similarity(
        self, claim_statement: str, records: list[EvidenceRecord]
    ) -> list[float]:
        if self.llm is None:
            return [0.0] * len(records)
        texts = [f"{r.title}\n{r.abstract[:1500]}".strip() for r in records]
        try:
            vectors = await self.llm.embed([claim_statement, *texts])
        except Exception as exc:  # embeddings are an optimisation, not a requirement
            log.warning("retrieval.embedding_failed", error=str(exc)[:200])
            return [0.0] * len(records)
        if len(vectors) != len(records) + 1:
            return [0.0] * len(records)
        claim_vector = vectors[0]
        return [max(0.0, cosine(claim_vector, v)) for v in vectors[1:]]

    async def aclose(self) -> None:
        await asyncio.gather(
            self.pubmed.aclose(),
            self.europepmc.aclose(),
            self.clinicaltrials.aclose(),
            return_exceptions=True,
        )


# ------------------------------------------------------------------ scoring ---
def deduplicate(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Collapse the same work retrieved from multiple sources.

    PubMed records win over Europe PMC for the same DOI/PMID because their
    MeSH indexing and publication types drive downstream scoring; Europe PMC
    citation counts are merged in.
    """
    best: dict[str, EvidenceRecord] = {}
    for record in records:
        key = record.dedupe_key
        existing = best.get(key)
        if existing is None:
            best[key] = record
            continue
        winner, loser = _preferred(existing, record)
        if winner.citation_count is None and loser.citation_count is not None:
            winner.citation_count = loser.citation_count
        if not winner.abstract and loser.abstract:
            winner.abstract = loser.abstract
        if not winner.doi and loser.doi:
            winner.doi = loser.doi
        if not winner.mesh_terms and loser.mesh_terms:
            winner.mesh_terms = loser.mesh_terms
        best[key] = winner
    return list(best.values())


_SOURCE_PRIORITY = {
    EvidenceSource.PUBMED: 3,
    EvidenceSource.CLINICALTRIALS_GOV: 2,
    EvidenceSource.EUROPE_PMC: 1,
    EvidenceSource.OPENALEX: 0,
}


def _preferred(a: EvidenceRecord, b: EvidenceRecord) -> tuple[EvidenceRecord, EvidenceRecord]:
    if _SOURCE_PRIORITY.get(a.source, 0) >= _SOURCE_PRIORITY.get(b.source, 0):
        return a, b
    return b, a


def lexical_relevance(claim: str, record: EvidenceRecord) -> float:
    haystack = normalize_for_match(f"{record.title} {record.abstract[:2000]}")
    needle = normalize_for_match(claim)
    if not haystack or not needle:
        return 0.0
    return round(fuzz.token_set_ratio(needle, haystack) / 100.0, 4)


def quality_score(record: EvidenceRecord) -> float:
    """0-1 assessment of how much weight this record deserves."""
    if record.is_retracted:
        return 0.0

    design = PUBLICATION_TYPE_WEIGHT.get(record.study_design, 0.35)

    # Half-life of ~12 years: older work still counts, just less.
    age = record.age_years
    recency = 0.5 if age is None else round(math.exp(-age / 12.0), 4)

    if record.citation_count is None:
        citations = 0.4  # unknown, not zero
    else:
        citations = min(1.0, math.log10(record.citation_count + 1) / 3.0)

    completeness = 1.0 if record.has_usable_abstract else 0.35
    preprint_penalty = 0.85 if record.is_preprint else 1.0

    score = 0.45 * design + 0.20 * recency + 0.15 * citations + 0.20 * completeness
    return round(min(1.0, score * preprint_penalty), 4)


def diversify(scored: list[ScoredRecord], limit: int) -> list[ScoredRecord]:
    """Keep the top records while guaranteeing source and stance diversity.

    A claim adjudicated against eight near-identical reviews from one journal
    tells an investor nothing.  We reserve slots for registry records and for
    the highest-quality record from each source.
    """
    if len(scored) <= limit:
        return scored

    selected: list[ScoredRecord] = []
    seen_sources: set[EvidenceSource] = set()

    # First pass: best record from each source.
    for item in scored:
        if item.record.source not in seen_sources:
            selected.append(item)
            seen_sources.add(item.record.source)
        if len(selected) >= limit:
            return selected

    # Second pass: fill by rank, skipping near-duplicate titles.
    chosen_titles = [normalize_for_match(i.record.title) for i in selected]
    for item in scored:
        if item in selected:
            continue
        title = normalize_for_match(item.record.title)
        if any(fuzz.ratio(title, existing) > 92 for existing in chosen_titles):
            continue
        selected.append(item)
        chosen_titles.append(title)
        if len(selected) >= limit:
            break
    return selected[:limit]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def summarise_stats(results: list[RetrievalResult]) -> dict[str, Any]:
    return {
        "claims_searched": len(results),
        "records_retained": sum(len(r.records) for r in results),
        "records_before_ranking": sum(r.total_before_ranking for r in results),
        "claims_with_no_evidence": sum(1 for r in results if not r.records),
        "source_errors": sum(len(r.errors) for r in results),
    }
