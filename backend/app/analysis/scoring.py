"""Stage 8: credibility scoring.

Scores are computed **in code**, not by a language model.  The model supplies
per-item judgements (stance, relevance, strength); this module aggregates them
with fixed, inspectable arithmetic.  That matters for a diligence product: an
investment committee can be shown exactly why a claim scored 41 and not 72, and
the same inputs always produce the same number.

Every score ships with a ``breakdown`` dictionary containing its components.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.analysis.adjudicator import AdjudicatedEvidence, ClaimAdjudication
from app.core.enums import (
    EVIDENCE_TIER_WEIGHT,
    ClaimCategory,
    CredibilityBand,
    EvidenceTier,
    QuoteVerification,
    Stance,
)
from app.evidence.retriever import quality_score

# --------------------------------------------------------------- constants ---
#: Evidence mass at which the external component saturates.
SUPPORT_SCALE = 1.2
#: Evidence mass at which we consider the search to have been conclusive.
CONFIDENCE_SCALE = 1.0
#: Weight split between what the company brings and what the literature says.
W_INTERNAL = 0.45
W_EXTERNAL = 0.55
#: Contradicting evidence counts for more than supporting evidence of equal
#: weight: disconfirmation is more informative than confirmation.
CONTRADICTION_MULTIPLIER = 1.35

BAND_THRESHOLDS: tuple[tuple[float, CredibilityBand], ...] = (
    (75.0, CredibilityBand.STRONG),
    (60.0, CredibilityBand.MODERATE),
    (45.0, CredibilityBand.LIMITED),
    (30.0, CredibilityBand.WEAK),
    (0.0, CredibilityBand.UNSUPPORTED),
)

#: Claim categories whose failure most threatens an investment thesis.
CATEGORY_WEIGHT: dict[ClaimCategory, float] = {
    ClaimCategory.CLINICAL_EFFICACY: 1.00,
    ClaimCategory.PRECLINICAL_EFFICACY: 0.85,
    ClaimCategory.MECHANISM: 0.80,
    ClaimCategory.TARGET_VALIDATION: 0.85,
    ClaimCategory.SAFETY: 0.90,
    ClaimCategory.BIOMARKER: 0.65,
    ClaimCategory.PLATFORM: 0.60,
    ClaimCategory.MANUFACTURING: 0.55,
    ClaimCategory.REGULATORY: 0.55,
    ClaimCategory.IP: 0.45,
    ClaimCategory.COMPETITIVE: 0.40,
    ClaimCategory.MARKET: 0.20,
    ClaimCategory.OTHER: 0.30,
}


@dataclass(slots=True)
class ClaimScoringInput:
    """Everything the scorer needs about one claim."""

    claim_id: str
    category: ClaimCategory
    claimed_tier: EvidenceTier
    importance: float
    is_thesis_critical: bool
    hedging_language: bool
    quote_verification: QuoteVerification
    extraction_confidence: float
    #: Number of quantitative details that include n / p-value / comparator.
    has_sample_size: bool = False
    has_statistics: bool = False
    has_comparator: bool = False
    has_effect_size: bool = False


@dataclass(slots=True)
class ClaimScore:
    claim_id: str
    credibility_score: float
    band: CredibilityBand
    confidence: float
    supporting_count: int
    contradicting_count: int
    neutral_count: int
    evidence_quality: float
    consistency: float
    breakdown: dict[str, Any] = field(default_factory=dict)


def score_claim(claim: ClaimScoringInput, adjudication: ClaimAdjudication | None) -> ClaimScore:
    """Compute a claim's credibility from its own rigour and external evidence."""
    supporting = adjudication.supporting if adjudication else []
    contradicting = adjudication.contradicting if adjudication else []
    mixed = adjudication.mixed if adjudication else []
    neutral = adjudication.neutral if adjudication else []

    # ---- what the company itself brings ---------------------------------
    tier_weight = EVIDENCE_TIER_WEIGHT.get(claim.claimed_tier, 0.0)
    rigor = _rigor(claim)
    internal = tier_weight * (0.55 + 0.45 * rigor)
    if claim.hedging_language:
        internal *= 0.88
    if claim.quote_verification is QuoteVerification.FUZZY:
        internal *= 0.95
    internal = _clamp(internal)

    # ---- what the literature says ---------------------------------------
    support_mass = sum(e.weighted_strength for e in supporting) + 0.5 * sum(
        e.weighted_strength for e in mixed
    )
    contra_mass = (
        sum(e.weighted_strength for e in contradicting)
        + 0.5 * sum(e.weighted_strength for e in mixed)
    ) * CONTRADICTION_MULTIPLIER
    net = support_mass - contra_mass
    external_raw = 0.5 + 0.5 * math.tanh(net / SUPPORT_SCALE)

    total_mass = support_mass + contra_mass
    evidence_confidence = 1.0 - math.exp(-total_mass / CONFIDENCE_SCALE)
    # With no external evidence the external term must stay neutral rather
    # than penalise the claim: absence of evidence is reported separately.
    external = 0.5 + (external_raw - 0.5) * evidence_confidence

    score = 100.0 * _clamp(W_INTERNAL * internal + W_EXTERNAL * external)

    # ---- confidence in the score itself ---------------------------------
    quality = _mean([quality_score(e.record) for e in (*supporting, *contradicting, *mixed)])
    consistency = _consistency(supporting, contradicting, mixed)
    confidence = _clamp(
        0.40 * evidence_confidence
        + 0.20 * consistency
        + 0.20 * claim.extraction_confidence
        + 0.20 * (1.0 if claim.quote_verification is QuoteVerification.EXACT else 0.6)
    )

    return ClaimScore(
        claim_id=claim.claim_id,
        credibility_score=round(score, 2),
        band=band_for(score),
        confidence=round(confidence, 4),
        supporting_count=len(supporting),
        contradicting_count=len(contradicting),
        neutral_count=len(neutral),
        evidence_quality=round(quality, 4),
        consistency=round(consistency, 4),
        breakdown={
            "internal_component": round(internal, 4),
            "external_component": round(external, 4),
            "claimed_tier": claim.claimed_tier.value,
            "tier_weight": round(tier_weight, 4),
            "rigor": round(rigor, 4),
            "support_mass": round(support_mass, 4),
            "contradiction_mass": round(contra_mass, 4),
            "net_evidence": round(net, 4),
            "evidence_confidence": round(evidence_confidence, 4),
            "mixed_count": len(mixed),
            "weights": {"internal": W_INTERNAL, "external": W_EXTERNAL},
            "hedging_penalty_applied": claim.hedging_language,
        },
    )


def _rigor(claim: ClaimScoringInput) -> float:
    """How well-specified the company's own evidence is."""
    signals = (
        claim.has_sample_size,
        claim.has_statistics,
        claim.has_comparator,
        claim.has_effect_size,
    )
    return sum(1.0 for s in signals if s) / len(signals)


def _consistency(
    supporting: list[AdjudicatedEvidence],
    contradicting: list[AdjudicatedEvidence],
    mixed: list[AdjudicatedEvidence],
) -> float:
    """1 when the evidence all points one way, 0 when it is evenly split."""
    total = len(supporting) + len(contradicting) + len(mixed)
    if total == 0:
        return 0.0
    dominant = max(len(supporting), len(contradicting))
    return round(dominant / total, 4)


def band_for(score: float) -> CredibilityBand:
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return CredibilityBand.UNSUPPORTED


# ------------------------------------------------------------ run-level ---
@dataclass(slots=True)
class OverallScore:
    score: float
    band: CredibilityBand
    confidence: float
    breakdown: dict[str, Any] = field(default_factory=dict)


def score_run(
    claim_scores: list[ClaimScore],
    claim_inputs: dict[str, ClaimScoringInput],
    *,
    pages_analysed: int = 0,
    pages_total: int = 0,
    claims_needing_review: int = 0,
) -> OverallScore:
    """Aggregate claim scores into one scientific-credibility number.

    Weighting is by importance and category, not a flat mean: a contradicted
    mechanism claim must outweigh five corroborated market claims.
    """
    if not claim_scores:
        return OverallScore(
            score=0.0,
            band=CredibilityBand.UNSUPPORTED,
            confidence=0.0,
            breakdown={"reason": "No claims were extracted from the document."},
        )

    weighted_sum = 0.0
    weight_total = 0.0
    for score in claim_scores:
        claim = claim_inputs.get(score.claim_id)
        if claim is None:
            continue
        weight = (
            CATEGORY_WEIGHT.get(claim.category, 0.3)
            * (0.35 + 0.65 * claim.importance)
            * (1.6 if claim.is_thesis_critical else 1.0)
        )
        weighted_sum += score.credibility_score * weight
        weight_total += weight

    base = weighted_sum / weight_total if weight_total else 0.0

    # Penalties applied after aggregation so they are visible in the breakdown.
    critical_contradicted = sum(
        1
        for s in claim_scores
        if s.contradicting_count > 0
        and (claim_inputs.get(s.claim_id) or _NULL_CLAIM).is_thesis_critical
    )
    critical_unsupported = sum(
        1
        for s in claim_scores
        if s.supporting_count == 0
        and (claim_inputs.get(s.claim_id) or _NULL_CLAIM).is_thesis_critical
    )
    contradiction_penalty = min(18.0, 6.0 * critical_contradicted)
    unsupported_penalty = min(12.0, 3.0 * critical_unsupported)

    coverage = (pages_analysed / pages_total) if pages_total else 1.0
    coverage_penalty = max(0.0, (1.0 - coverage) * 10.0)

    adjusted = base - contradiction_penalty - unsupported_penalty - coverage_penalty
    final = _clamp(adjusted / 100.0) * 100.0

    mean_confidence = _mean([s.confidence for s in claim_scores])
    review_ratio = claims_needing_review / len(claim_scores)
    overall_confidence = _clamp(
        0.6 * mean_confidence + 0.25 * coverage + 0.15 * (1.0 - review_ratio)
    )

    return OverallScore(
        score=round(final, 2),
        band=band_for(final),
        confidence=round(overall_confidence, 4),
        breakdown={
            "weighted_base": round(base, 2),
            "claims_scored": len(claim_scores),
            "thesis_critical_claims": sum(1 for c in claim_inputs.values() if c.is_thesis_critical),
            "thesis_critical_contradicted": critical_contradicted,
            "thesis_critical_unsupported": critical_unsupported,
            "penalties": {
                "contradiction": round(contradiction_penalty, 2),
                "unsupported_critical": round(unsupported_penalty, 2),
                "page_coverage": round(coverage_penalty, 2),
            },
            "page_coverage": round(coverage, 4),
            "claims_needing_review": claims_needing_review,
            "mean_claim_confidence": round(mean_confidence, 4),
            "band_thresholds": {band.value: threshold for threshold, band in BAND_THRESHOLDS},
        },
    )


_NULL_CLAIM = ClaimScoringInput(
    claim_id="",
    category=ClaimCategory.OTHER,
    claimed_tier=EvidenceTier.NONE_STATED,
    importance=0.0,
    is_thesis_critical=False,
    hedging_language=False,
    quote_verification=QuoteVerification.NOT_APPLICABLE,
    extraction_confidence=0.0,
)


def stance_counts(adjudication: ClaimAdjudication | None) -> dict[str, int]:
    if adjudication is None:
        return dict.fromkeys((s.value for s in Stance), 0)
    counts = dict.fromkeys((s.value for s in Stance), 0)
    for evidence in adjudication.evidence:
        counts[evidence.stance.value] += 1
    return counts


# ------------------------------------------------------------------ utils ---
def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _mean(values: list[Any]) -> float:
    numbers = [float(v) for v in values if isinstance(v, int | float)]
    return sum(numbers) / len(numbers) if numbers else 0.0
