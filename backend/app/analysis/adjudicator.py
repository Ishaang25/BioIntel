"""Stage 7: adjudicate each claim against its retrieved evidence.

This is where a due-diligence tool earns or loses its credibility, so the
design is defensive:

* Records are adjudicated in **batches per claim**, so the model compares them
  against each other rather than judging each in isolation.
* The model must quote the abstract it was shown.  That quote is verified
  against the abstract text; an unverifiable quote **downgrades the stance to
  neutral** rather than being silently accepted.
* Stance strength is combined with a deterministic quality score computed from
  study design, recency and citations -- the model does not get to inflate the
  weight of a weak paper.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.enums import PUBLICATION_TYPE_WEIGHT, QuoteVerification, Stance
from app.core.logging import get_logger
from app.evidence.models import EvidenceRecord
from app.evidence.retriever import ScoredRecord, quality_score
from app.llm import prompts
from app.llm.client import LLMClient
from app.llm.schemas import BatchAdjudicationItem, BatchAdjudicationOut
from app.utils.text import truncate, verify_quote

log = get_logger(__name__)

#: Records adjudicated in one model call.  Small enough that every abstract
#: gets real attention, large enough for cross-record comparison.
BATCH_SIZE = 6
#: Below this relevance a record is not worth an analyst's attention.
MIN_RELEVANCE_TO_KEEP = 0.12


@dataclass(slots=True)
class AdjudicatedEvidence:
    record: EvidenceRecord
    stance: Stance
    relevance: float
    strength: float
    similarity: float
    rationale: str
    supporting_quote: str
    quote_verification: QuoteVerification
    quote_match_score: float
    caveats: list[str] = field(default_factory=list)
    retrieval_query: str | None = None
    #: Quality-weighted contribution used by the scoring engine.
    weighted_strength: float = 0.0

    @property
    def is_decision_relevant(self) -> bool:
        return (
            self.stance in (Stance.SUPPORTS, Stance.CONTRADICTS, Stance.MIXED)
            and self.relevance >= MIN_RELEVANCE_TO_KEEP
        )


@dataclass(slots=True)
class ClaimAdjudication:
    claim_id: str
    evidence: list[AdjudicatedEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def supporting(self) -> list[AdjudicatedEvidence]:
        return [e for e in self.evidence if e.stance is Stance.SUPPORTS]

    @property
    def contradicting(self) -> list[AdjudicatedEvidence]:
        return [e for e in self.evidence if e.stance is Stance.CONTRADICTS]

    @property
    def mixed(self) -> list[AdjudicatedEvidence]:
        return [e for e in self.evidence if e.stance is Stance.MIXED]

    @property
    def neutral(self) -> list[AdjudicatedEvidence]:
        return [e for e in self.evidence if e.stance is Stance.NEUTRAL]

    def best(self, stance: Stance) -> AdjudicatedEvidence | None:
        candidates = [e for e in self.evidence if e.stance is stance]
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.weighted_strength)


class Adjudicator:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def adjudicate_claim(
        self,
        *,
        claim_id: str,
        claim_statement: str,
        claim_quote: str,
        claim_category: str,
        claimed_tier: str,
        records: list[ScoredRecord],
        claim_type: str = "other",
        corroboration_guidance: str = "",
    ) -> ClaimAdjudication:
        result = ClaimAdjudication(claim_id=claim_id)
        if not records:
            return result

        batches = [records[i : i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]
        outcomes = await asyncio.gather(
            *(
                self._adjudicate_batch(
                    claim_statement=claim_statement,
                    claim_quote=claim_quote,
                    claim_category=claim_category,
                    claim_type=claim_type,
                    corroboration_guidance=corroboration_guidance,
                    claimed_tier=claimed_tier,
                    batch=batch,
                    batch_index=index,
                )
                for index, batch in enumerate(batches)
            ),
            return_exceptions=True,
        )

        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                result.errors.append(str(outcome)[:300])
                log.warning(
                    "adjudication.batch_failed", claim_id=claim_id, error=str(outcome)[:300]
                )
                continue
            result.evidence.extend(outcome)

        result.evidence.sort(key=lambda e: e.weighted_strength, reverse=True)
        return result

    async def _adjudicate_batch(
        self,
        *,
        claim_statement: str,
        claim_quote: str,
        claim_category: str,
        claim_type: str,
        corroboration_guidance: str,
        claimed_tier: str,
        batch: list[ScoredRecord],
        batch_index: int,
    ) -> list[AdjudicatedEvidence]:
        refs = {f"E{batch_index * BATCH_SIZE + i + 1}": scored for i, scored in enumerate(batch)}
        prompt_records = [scored.record.to_prompt_dict(ref) for ref, scored in refs.items()]

        output = await self.llm.structured(
            purpose="adjudication",
            stage="adjudication",
            system=prompts.system(),
            user=prompts.render(
                "adjudication",
                claim_statement=claim_statement,
                claim_quote=truncate(claim_quote, 600),
                claim_category=claim_category,
                claim_type=claim_type,
                corroboration_guidance=(
                    corroboration_guidance or "No specific corroboration guidance available."
                ),
                claimed_tier=claimed_tier,
                evidence=_format_evidence(prompt_records),
            ),
            schema=BatchAdjudicationOut,
            model=settings.model_reasoning,
            context={
                "claim_statement": claim_statement,
                "evidence": prompt_records,
            },
        )

        adjudicated: list[AdjudicatedEvidence] = []
        for item in output.adjudications:
            scored = refs.get(item.evidence_ref)
            if scored is None:
                # The model invented or mangled a reference; drop it rather
                # than guess which record it meant.
                log.warning("adjudication.unknown_ref", ref=item.evidence_ref)
                continue
            adjudicated.append(self._finalise(item, scored))
        return adjudicated

    def _finalise(self, item: BatchAdjudicationItem, scored: ScoredRecord) -> AdjudicatedEvidence:
        record = scored.record
        stance = item.stance
        caveats = list(item.caveats)

        verification, match_score = verify_quote(
            item.supporting_quote, f"{record.title} {record.abstract}"
        )

        if stance is not Stance.UNRELATED and verification is QuoteVerification.NOT_FOUND:
            # The justification could not be located in the abstract we sent.
            # Keep the record visible but strip its power to move the score.
            caveats.append(
                "Supporting quote could not be verified against the abstract; "
                "stance downgraded to neutral by BioIntel."
            )
            log.warning(
                "adjudication.quote_unverified",
                evidence=record.citation_label,
                claimed_stance=stance.value,
            )
            stance = Stance.NEUTRAL

        if record.is_retracted:
            caveats.append("This publication has been retracted.")
            stance = Stance.NEUTRAL

        quality = quality_score(record)
        design_weight = PUBLICATION_TYPE_WEIGHT.get(record.study_design, 0.35)
        if not record.has_usable_abstract:
            caveats.append("No usable abstract was available; judgement is based on title only.")

        # The model's strength is capped by what the study design can support.
        weighted = round(item.strength * item.relevance * (0.5 + 0.5 * design_weight) * quality, 4)

        return AdjudicatedEvidence(
            record=record,
            stance=stance,
            relevance=round(item.relevance, 4),
            strength=round(item.strength, 4),
            similarity=scored.similarity,
            rationale=item.rationale,
            supporting_quote=item.supporting_quote,
            quote_verification=verification,
            quote_match_score=match_score,
            caveats=caveats,
            retrieval_query=record.retrieval_query,
            weighted_strength=(
                weighted if stance is not Stance.NEUTRAL else round(weighted * 0.2, 4)
            ),
        )


def _format_evidence(records: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for record in records:
        lines = [
            f"--- {record['ref']} ---",
            f"Source: {record['source']} | Id: {record['id']}",
            f"Title: {record['title']}",
        ]
        if record.get("journal"):
            lines.append(f"Journal: {record['journal']} ({record.get('year') or 'year unknown'})")
        if record.get("publication_types"):
            lines.append(f"Publication types: {', '.join(record['publication_types'])}")
        if record.get("is_retracted"):
            lines.append("WARNING: this publication is marked as RETRACTED.")
        if record.get("is_preprint"):
            lines.append("NOTE: this is a preprint and has not been peer reviewed.")
        trial = record.get("trial")
        if trial:
            lines.append(
                "Trial: status={status}, phases={phases}, enrolment={enrollment}, "
                "results_posted={has_results}".format(
                    status=trial.get("status"),
                    phases=", ".join(trial.get("phases") or []) or "n/a",
                    enrollment=trial.get("enrollment"),
                    has_results=trial.get("has_results"),
                )
            )
            if trial.get("why_stopped"):
                lines.append(f"Reason stopped: {trial['why_stopped']}")
            if trial.get("primary_outcomes"):
                lines.append(f"Primary outcomes: {'; '.join(trial['primary_outcomes'][:3])}")
        if record.get("abstract"):
            lines.append(f"Abstract: {record['abstract']}")
        else:
            lines.append("Abstract: (not available)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
