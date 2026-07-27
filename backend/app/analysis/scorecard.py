"""The Investment Committee scorecard.

A single credibility number tells an investment committee almost nothing about
*which* risk it is taking.  A company can be scientifically excellent and
commercially unready; regulatorily de-risked with a thin platform; or a strong
platform whose lead asset has no clinical data.  Collapsing all of that into
"24/100" destroys the information an IC actually needs.

This module produces ten dimensions, each with a score, a confidence band, and
an explicit list of the findings that drove it, plus an overall recommendation.
Every dimension is computed from the claim-level results by fixed arithmetic
and cites the claims that moved it, so any number can be traced to its inputs.

Dimensions that have no supporting claims are reported as **not assessed**
rather than scored zero — the same discipline that governs claim scoring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.analysis.claim_policy import policy_for
from app.analysis.corroboration import CorroborationAssessment
from app.analysis.scoring import ClaimScore, ClaimScoringInput, band_for
from app.core.enums import (
    ADVERSE_CORROBORATION,
    ClaimType,
    CompanyArchetype,
    ConfidenceLevel,
    CorroborationStatus,
    CredibilityBand,
    EvidenceTier,
    ICRecommendation,
    ScoreDimension,
    confidence_level,
)

#: Human-readable names and the question each dimension answers.
DIMENSION_META: dict[ScoreDimension, tuple[str, str]] = {
    ScoreDimension.SCIENTIFIC_VALIDITY: (
        "Scientific validity",
        "Is the underlying biology sound and consistent with published science?",
    ),
    ScoreDimension.CLINICAL_MATURITY: (
        "Clinical maturity",
        "How far into human testing has this actually progressed?",
    ),
    ScoreDimension.REGULATORY_CONFIDENCE: (
        "Regulatory confidence",
        "How much regulatory de-risking is real and verifiable?",
    ),
    ScoreDimension.EVIDENCE_QUALITY: (
        "Evidence quality",
        "How strong is the evidence behind the claims, by study design?",
    ),
    ScoreDimension.EXECUTION_CREDIBILITY: (
        "Execution credibility",
        "Has this team delivered what it said it would?",
    ),
    ScoreDimension.PLATFORM_STRENGTH: (
        "Platform strength",
        "Does the platform generalise, or is it one asset with a story?",
    ),
    ScoreDimension.PIPELINE_DIVERSIFICATION: (
        "Pipeline diversification",
        "How concentrated is the risk in a single programme?",
    ),
    ScoreDimension.TRANSLATIONAL_RISK: (
        "Translational readiness",
        "How large is the gap between the evidence shown and the claim implied?",
    ),
    ScoreDimension.COMMERCIAL_READINESS: (
        "Commercial readiness",
        "How close is this to a product a payer will reimburse?",
    ),
    ScoreDimension.DISCLOSURE_QUALITY: (
        "Disclosure quality",
        "Does the deck disclose enough to be checked, or does it assert?",
    ),
}

#: Weight of each dimension in the overall recommendation, by archetype.
#: A platform company lives or dies on platform generalisability; a
#: commercial-stage company on regulatory and commercial execution.
ARCHETYPE_WEIGHTS: dict[CompanyArchetype, dict[ScoreDimension, float]] = {
    CompanyArchetype.PLATFORM: {
        ScoreDimension.SCIENTIFIC_VALIDITY: 1.0,
        ScoreDimension.PLATFORM_STRENGTH: 1.3,
        ScoreDimension.PIPELINE_DIVERSIFICATION: 1.0,
        ScoreDimension.CLINICAL_MATURITY: 0.9,
        ScoreDimension.EVIDENCE_QUALITY: 1.0,
        ScoreDimension.TRANSLATIONAL_RISK: 0.9,
        ScoreDimension.REGULATORY_CONFIDENCE: 0.8,
        ScoreDimension.EXECUTION_CREDIBILITY: 0.9,
        ScoreDimension.COMMERCIAL_READINESS: 0.5,
        ScoreDimension.DISCLOSURE_QUALITY: 0.6,
    },
    CompanyArchetype.COMMERCIAL_STAGE: {
        ScoreDimension.REGULATORY_CONFIDENCE: 1.3,
        ScoreDimension.COMMERCIAL_READINESS: 1.2,
        ScoreDimension.CLINICAL_MATURITY: 1.1,
        ScoreDimension.EXECUTION_CREDIBILITY: 1.1,
        ScoreDimension.PIPELINE_DIVERSIFICATION: 1.0,
        ScoreDimension.EVIDENCE_QUALITY: 0.9,
        ScoreDimension.SCIENTIFIC_VALIDITY: 0.8,
        ScoreDimension.PLATFORM_STRENGTH: 0.7,
        ScoreDimension.TRANSLATIONAL_RISK: 0.6,
        ScoreDimension.DISCLOSURE_QUALITY: 0.6,
    },
    CompanyArchetype.CLINICAL_STAGE_ASSET: {
        ScoreDimension.CLINICAL_MATURITY: 1.3,
        ScoreDimension.EVIDENCE_QUALITY: 1.2,
        ScoreDimension.SCIENTIFIC_VALIDITY: 1.1,
        ScoreDimension.TRANSLATIONAL_RISK: 1.1,
        ScoreDimension.REGULATORY_CONFIDENCE: 1.0,
        ScoreDimension.EXECUTION_CREDIBILITY: 0.8,
        ScoreDimension.COMMERCIAL_READINESS: 0.7,
        ScoreDimension.PIPELINE_DIVERSIFICATION: 0.6,
        ScoreDimension.PLATFORM_STRENGTH: 0.5,
        ScoreDimension.DISCLOSURE_QUALITY: 0.7,
    },
    CompanyArchetype.PRECLINICAL_ASSET: {
        ScoreDimension.SCIENTIFIC_VALIDITY: 1.3,
        ScoreDimension.TRANSLATIONAL_RISK: 1.3,
        ScoreDimension.EVIDENCE_QUALITY: 1.1,
        ScoreDimension.PLATFORM_STRENGTH: 0.9,
        ScoreDimension.DISCLOSURE_QUALITY: 0.9,
        ScoreDimension.CLINICAL_MATURITY: 0.7,
        ScoreDimension.EXECUTION_CREDIBILITY: 0.7,
        ScoreDimension.PIPELINE_DIVERSIFICATION: 0.6,
        ScoreDimension.REGULATORY_CONFIDENCE: 0.5,
        ScoreDimension.COMMERCIAL_READINESS: 0.3,
    },
}
ARCHETYPE_WEIGHTS[CompanyArchetype.TOOLS_AND_SERVICES] = ARCHETYPE_WEIGHTS[
    CompanyArchetype.PLATFORM
]
ARCHETYPE_WEIGHTS[CompanyArchetype.DIAGNOSTICS] = ARCHETYPE_WEIGHTS[
    CompanyArchetype.CLINICAL_STAGE_ASSET
]
ARCHETYPE_WEIGHTS[CompanyArchetype.UNKNOWN] = ARCHETYPE_WEIGHTS[
    CompanyArchetype.CLINICAL_STAGE_ASSET
]


@dataclass(slots=True)
class DimensionScore:
    dimension: ScoreDimension
    label: str
    question: str
    #: 0-100, or None when no claim informed this dimension.
    score: float | None
    confidence: float
    #: Why the score is what it is, in plain language.
    rationale: str
    #: Findings that pushed the score up, as claim ids with a short reason.
    positive_drivers: list[dict[str, str]] = field(default_factory=list)
    #: Findings that pushed it down.
    negative_drivers: list[dict[str, str]] = field(default_factory=list)
    claims_considered: int = 0

    @property
    def assessed(self) -> bool:
        return self.score is not None

    @property
    def band(self) -> CredibilityBand | None:
        return band_for(self.score) if self.score is not None else None

    @property
    def confidence_band(self) -> ConfidenceLevel:
        return confidence_level(self.confidence)


@dataclass(slots=True)
class Scorecard:
    dimensions: list[DimensionScore]
    overall_score: float
    overall_band: CredibilityBand
    overall_confidence: float
    recommendation: ICRecommendation
    recommendation_rationale: str
    archetype: CompanyArchetype
    breakdown: dict[str, Any] = field(default_factory=dict)

    def by_dimension(self) -> dict[str, DimensionScore]:
        return {d.dimension.value: d for d in self.dimensions}

    def assessed_dimensions(self) -> list[DimensionScore]:
        return [d for d in self.dimensions if d.assessed]

    def weakest(self, limit: int = 3) -> list[DimensionScore]:
        return sorted(self.assessed_dimensions(), key=lambda d: d.score or 0.0)[:limit]

    def strongest(self, limit: int = 3) -> list[DimensionScore]:
        return sorted(self.assessed_dimensions(), key=lambda d: d.score or 0.0, reverse=True)[
            :limit
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "overall_band": self.overall_band.value,
            "overall_confidence": self.overall_confidence,
            "recommendation": self.recommendation.value,
            "recommendation_rationale": self.recommendation_rationale,
            "archetype": self.archetype.value,
            "dimensions": [
                {
                    "dimension": d.dimension.value,
                    "label": d.label,
                    "question": d.question,
                    "score": d.score,
                    "band": d.band.value if d.band else None,
                    "confidence": d.confidence,
                    "confidence_band": d.confidence_band.value,
                    "rationale": d.rationale,
                    "positive_drivers": d.positive_drivers,
                    "negative_drivers": d.negative_drivers,
                    "claims_considered": d.claims_considered,
                    "assessed": d.assessed,
                }
                for d in self.dimensions
            ],
            "breakdown": self.breakdown,
        }


@dataclass(slots=True)
class ScorecardInput:
    """One claim's contribution to the scorecard."""

    claim_id: str
    statement: str
    scoring: ClaimScoringInput
    score: ClaimScore
    corroboration: CorroborationAssessment | None


def detect_archetype(
    inputs: list[ScorecardInput],
    *,
    development_stage: str | None = None,
    pipeline_size: int = 0,
) -> CompanyArchetype:
    """Infer how this company should be assessed.

    Deliberately conservative: it only claims PLATFORM when the deck actually
    makes multiple platform-level assertions across a multi-programme
    pipeline, because assessing a single-asset company as a platform would
    understate its concentration risk.
    """
    platform_claims = sum(
        1 for i in inputs if i.scoring.claim_type is ClaimType.PLATFORM_CAPABILITY
    )
    approval_claims = sum(
        1
        for i in inputs
        if i.scoring.claim_type is ClaimType.REGULATORY_APPROVAL
        and i.score.corroboration
        not in (CorroborationStatus.CONTRADICTED, CorroborationStatus.DISPUTED)
    )
    clinical_claims = sum(
        1
        for i in inputs
        if i.scoring.claim_type in (ClaimType.CLINICAL_RESULT, ClaimType.PIPELINE_STAGE)
    )
    preclinical_only = all(
        i.scoring.claimed_tier
        in (
            EvidenceTier.NONE_STATED,
            EvidenceTier.IN_SILICO,
            EvidenceTier.IN_VITRO,
            EvidenceTier.IN_VIVO_ANIMAL,
            EvidenceTier.LITERATURE_ONLY,
        )
        for i in inputs
        if i.scoring.claim_type
        in (ClaimType.CLINICAL_RESULT, ClaimType.PRECLINICAL_RESULT, ClaimType.PIPELINE_STAGE)
    )

    if approval_claims >= 1 and clinical_claims >= 1:
        return CompanyArchetype.COMMERCIAL_STAGE
    if platform_claims >= 3 and pipeline_size >= 4:
        return CompanyArchetype.PLATFORM
    if clinical_claims >= 2 and not preclinical_only:
        return CompanyArchetype.CLINICAL_STAGE_ASSET
    if clinical_claims == 0 and inputs:
        return CompanyArchetype.PRECLINICAL_ASSET
    return CompanyArchetype.UNKNOWN


def build_scorecard(
    inputs: list[ScorecardInput],
    *,
    archetype: CompanyArchetype | None = None,
    pipeline_size: int = 0,
    development_stage: str | None = None,
    page_coverage: float = 1.0,
    marketing_claim_ratio: float = 0.0,
) -> Scorecard:
    """Compute the ten-dimension scorecard and the overall recommendation."""
    resolved_archetype = archetype or detect_archetype(
        inputs, development_stage=development_stage, pipeline_size=pipeline_size
    )

    dimensions = [
        _score_dimension(
            dimension, inputs, resolved_archetype, pipeline_size, marketing_claim_ratio
        )
        for dimension in ScoreDimension
    ]

    weights = ARCHETYPE_WEIGHTS.get(resolved_archetype, ARCHETYPE_WEIGHTS[CompanyArchetype.UNKNOWN])
    assessed = [d for d in dimensions if d.assessed]

    if assessed:
        weighted = sum((d.score or 0.0) * weights.get(d.dimension, 1.0) for d in assessed)
        total_weight = sum(weights.get(d.dimension, 1.0) for d in assessed)
        overall = weighted / total_weight if total_weight else 0.0
        confidence = sum(d.confidence for d in assessed) / len(assessed)
    else:
        overall, confidence = 0.0, 0.0

    confidence *= 0.7 + 0.3 * page_coverage

    recommendation, rationale = _recommend(overall, dimensions, inputs, confidence)

    return Scorecard(
        dimensions=dimensions,
        overall_score=round(overall, 2),
        overall_band=band_for(overall),
        overall_confidence=round(min(1.0, confidence), 4),
        recommendation=recommendation,
        recommendation_rationale=rationale,
        archetype=resolved_archetype,
        breakdown={
            "archetype": resolved_archetype.value,
            "archetype_weights": {k.value: v for k, v in weights.items()},
            "dimensions_assessed": len(assessed),
            "dimensions_not_assessed": [d.dimension.value for d in dimensions if not d.assessed],
            "claims_total": len(inputs),
            "claims_scored": sum(1 for i in inputs if i.score.scored),
            "claims_excluded": sum(1 for i in inputs if not i.score.scored),
            "page_coverage": round(page_coverage, 4),
            "methodology": (
                "Each dimension aggregates the claims that inform it, weighted by claim "
                "importance and evidence grade. Dimensions with no informing claims are "
                "reported as not assessed rather than scored zero."
            ),
        },
    )


# --------------------------------------------------------------- per axis ---
def _score_dimension(
    dimension: ScoreDimension,
    inputs: list[ScorecardInput],
    archetype: CompanyArchetype,
    pipeline_size: int,
    marketing_ratio: float,
) -> DimensionScore:
    label, question = DIMENSION_META[dimension]

    # Two dimensions are structural rather than claim-aggregates.
    if dimension is ScoreDimension.PIPELINE_DIVERSIFICATION:
        return _pipeline_dimension(label, question, inputs, pipeline_size)
    if dimension is ScoreDimension.DISCLOSURE_QUALITY:
        return _disclosure_dimension(label, question, inputs, marketing_ratio)

    contributions: list[tuple[ScorecardInput, float]] = []
    for item in inputs:
        if not item.score.scored:
            continue
        weight = policy_for(item.scoring.claim_type).dimensions.get(dimension, 0.0)
        if weight <= 0:
            continue
        # Importance modulates weight so a peripheral claim cannot dominate.
        contributions.append((item, weight * (0.4 + 0.6 * item.scoring.importance)))

    if not contributions:
        return DimensionScore(
            dimension=dimension,
            label=label,
            question=question,
            score=None,
            confidence=0.0,
            rationale=(
                f"Not assessed: the document made no claims that bear on {label.lower()}. "
                "This is a gap in the deck, not a negative finding."
            ),
        )

    weighted = sum(item.score.credibility_score * w for item, w in contributions)
    total = sum(w for _, w in contributions)
    score = weighted / total

    if dimension is ScoreDimension.TRANSLATIONAL_RISK:
        score = _apply_translational_adjustment(score, contributions)
    if dimension is ScoreDimension.CLINICAL_MATURITY:
        score = _apply_maturity_adjustment(score, inputs)
    if dimension is ScoreDimension.PLATFORM_STRENGTH:
        score = _apply_platform_coherence(score, inputs, archetype)

    positives, negatives = _drivers(contributions)
    confidence = sum(item.score.confidence * w for item, w in contributions) / total

    return DimensionScore(
        dimension=dimension,
        label=label,
        question=question,
        score=round(_clamp100(score), 2),
        confidence=round(confidence, 4),
        rationale=_dimension_rationale(
            dimension, label, score, positives, negatives, len(contributions)
        ),
        positive_drivers=positives,
        negative_drivers=negatives,
        claims_considered=len(contributions),
    )


def _apply_translational_adjustment(
    score: float, contributions: list[tuple[ScorecardInput, float]]
) -> float:
    """Penalise clinical claims resting on preclinical evidence.

    This axis is scored as *readiness*: higher is better, so a large gap
    between the tier shown and the claim implied pulls it down.
    """
    gaps = sum(
        1
        for item, _ in contributions
        if item.scoring.claim_type is ClaimType.CLINICAL_RESULT
        and item.scoring.claimed_tier
        in (
            EvidenceTier.NONE_STATED,
            EvidenceTier.IN_SILICO,
            EvidenceTier.IN_VITRO,
            EvidenceTier.IN_VIVO_ANIMAL,
        )
    )
    return score - min(20.0, 6.0 * gaps)


def _apply_maturity_adjustment(score: float, inputs: list[ScorecardInput]) -> float:
    """Anchor clinical maturity to the furthest stage actually evidenced."""
    best_tier = max(
        (i.scoring.claimed_tier for i in inputs),
        key=lambda t: _TIER_RANK.get(t, 0),
        default=EvidenceTier.NONE_STATED,
    )
    ceiling = {
        EvidenceTier.APPROVED: 100.0,
        EvidenceTier.CLINICAL_PHASE_3: 92.0,
        EvidenceTier.CLINICAL_PHASE_2: 78.0,
        EvidenceTier.CLINICAL_PHASE_1: 62.0,
        EvidenceTier.EX_VIVO_HUMAN: 48.0,
        EvidenceTier.IN_VIVO_ANIMAL: 42.0,
        EvidenceTier.IN_VITRO: 34.0,
        EvidenceTier.IN_SILICO: 26.0,
        EvidenceTier.LITERATURE_ONLY: 30.0,
        EvidenceTier.NONE_STATED: 100.0,  # no ceiling if no tier was stated
    }.get(best_tier, 100.0)
    return min(score, ceiling)


def _apply_platform_coherence(
    score: float, inputs: list[ScorecardInput], archetype: CompanyArchetype
) -> float:
    """Platform claims are evidenced by the portfolio, not individually.

    A platform company whose *assets* are broadly corroborated has, by that
    fact, evidenced its platform — even if no single "our platform works"
    statement was individually confirmable. This is the platform-company
    treatment the single-claim model could not express.
    """
    asset_claims = [
        i
        for i in inputs
        if i.score.scored
        and i.scoring.claim_type
        in (
            ClaimType.CLINICAL_RESULT,
            ClaimType.PIPELINE_STAGE,
            ClaimType.REGULATORY_APPROVAL,
            ClaimType.PRECLINICAL_RESULT,
        )
    ]
    if len(asset_claims) < 3:
        return score

    corroborated = sum(
        1
        for i in asset_claims
        if i.score.corroboration
        in (CorroborationStatus.CORROBORATED, CorroborationStatus.PARTIALLY_CORROBORATED)
    )
    coherence = corroborated / len(asset_claims)
    # Up to +18 points when the portfolio itself demonstrates the platform.
    lift = 18.0 * coherence * (1.2 if archetype is CompanyArchetype.PLATFORM else 0.7)
    return score + lift


_TIER_RANK = {
    EvidenceTier.NONE_STATED: 0,
    EvidenceTier.LITERATURE_ONLY: 1,
    EvidenceTier.IN_SILICO: 2,
    EvidenceTier.IN_VITRO: 3,
    EvidenceTier.EX_VIVO_HUMAN: 4,
    EvidenceTier.IN_VIVO_ANIMAL: 5,
    EvidenceTier.CLINICAL_PHASE_1: 6,
    EvidenceTier.CLINICAL_PHASE_2: 7,
    EvidenceTier.CLINICAL_PHASE_3: 8,
    EvidenceTier.APPROVED: 9,
}


def _pipeline_dimension(
    label: str, question: str, inputs: list[ScorecardInput], pipeline_size: int
) -> DimensionScore:
    """Concentration risk: how much rests on a single programme."""
    programmes = pipeline_size
    if programmes == 0:
        return DimensionScore(
            dimension=ScoreDimension.PIPELINE_DIVERSIFICATION,
            label=label,
            question=question,
            score=None,
            confidence=0.0,
            rationale=(
                "Not assessed: no pipeline was disclosed in the document, so concentration "
                "risk cannot be evaluated."
            ),
        )

    # Saturating: the difference between 1 and 3 programmes matters far more
    # than the difference between 8 and 10.
    score = 100.0 * (1.0 - math.exp(-programmes / 3.2))
    negatives: list[dict[str, str]] = []
    positives: list[dict[str, str]] = []
    if programmes <= 2:
        negatives.append(
            {
                "claim_id": "",
                "reason": (
                    f"Only {programmes} disclosed programme(s): failure of the lead asset "
                    "would be close to terminal for the thesis."
                ),
            }
        )
    else:
        positives.append(
            {
                "claim_id": "",
                "reason": f"{programmes} disclosed programmes spread technical risk across assets.",
            }
        )

    return DimensionScore(
        dimension=ScoreDimension.PIPELINE_DIVERSIFICATION,
        label=label,
        question=question,
        score=round(score, 2),
        confidence=0.7,
        rationale=(
            f"{programmes} programme(s) were disclosed. "
            + (
                "Risk is concentrated in very few assets, so a single technical failure "
                "would be difficult to absorb."
                if programmes <= 2
                else "Risk is spread across enough programmes that a single failure need not "
                "be terminal, provided they do not share a common failure mode."
            )
        ),
        positive_drivers=positives,
        negative_drivers=negatives,
        claims_considered=programmes,
    )


def _disclosure_dimension(
    label: str, question: str, inputs: list[ScorecardInput], marketing_ratio: float
) -> DimensionScore:
    """Whether the deck gives an analyst enough to check it."""
    if not inputs:
        return DimensionScore(
            dimension=ScoreDimension.DISCLOSURE_QUALITY,
            label=label,
            question=question,
            score=None,
            confidence=0.0,
            rationale="Not assessed: no claims were extracted.",
        )

    quantitative = [i for i in inputs if i.scoring.has_effect_size]
    with_stats = sum(1 for i in quantitative if i.scoring.has_statistics)
    with_n = sum(1 for i in quantitative if i.scoring.has_sample_size)
    with_control = sum(1 for i in quantitative if i.scoring.has_comparator)
    hedged = sum(1 for i in inputs if i.scoring.hedging_language)

    if quantitative:
        rigour = (with_stats + with_n + with_control) / (3 * len(quantitative))
    else:
        rigour = 0.35  # nothing quantitative to check is itself a disclosure gap

    score = 100.0 * max(
        0.0,
        min(
            1.0,
            0.55 * rigour + 0.30 * (1.0 - marketing_ratio) + 0.15 * (1.0 - hedged / len(inputs)),
        ),
    )

    negatives: list[dict[str, str]] = []
    positives: list[dict[str, str]] = []
    if quantitative and with_stats / len(quantitative) < 0.4:
        negatives.append(
            {
                "claim_id": "",
                "reason": (
                    f"Only {with_stats} of {len(quantitative)} quantitative claims report any "
                    "statistical support."
                ),
            }
        )
    if marketing_ratio > 0.25:
        negatives.append(
            {
                "claim_id": "",
                "reason": f"{marketing_ratio:.0%} of extracted statements are promotional rather than factual.",
            }
        )
    if quantitative and with_n / len(quantitative) >= 0.6:
        positives.append(
            {"claim_id": "", "reason": "Most quantitative claims state a sample size."}
        )

    return DimensionScore(
        dimension=ScoreDimension.DISCLOSURE_QUALITY,
        label=label,
        question=question,
        score=round(score, 2),
        confidence=0.75,
        rationale=(
            f"Of {len(quantitative)} quantitative claim(s), {with_stats} report statistics, "
            f"{with_n} report a sample size and {with_control} name a comparator. "
            f"{marketing_ratio:.0%} of extracted statements are promotional. Disclosure quality "
            "measures whether an analyst can check the deck, not whether the science is good."
        ),
        positive_drivers=positives,
        negative_drivers=negatives,
        claims_considered=len(inputs),
    )


def _drivers(
    contributions: list[tuple[ScorecardInput, float]], limit: int = 4
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    ranked = sorted(contributions, key=lambda pair: pair[1], reverse=True)
    positives: list[dict[str, str]] = []
    negatives: list[dict[str, str]] = []

    for item, _ in ranked:
        entry = {
            "claim_id": item.claim_id,
            "statement": item.statement[:180],
            "score": f"{item.score.credibility_score:.0f}",
            "status": item.score.corroboration.value,
        }
        if item.score.corroboration in ADVERSE_CORROBORATION:
            entry["reason"] = "Retrieved evidence disagrees with this claim."
            negatives.append(entry)
        elif item.score.credibility_score >= 65:
            entry["reason"] = (
                item.corroboration.rationale[:200]
                if item.corroboration
                else "Well-supported claim."
            )
            positives.append(entry)
        elif item.score.credibility_score < 40:
            entry["reason"] = (
                item.corroboration.rationale[:200]
                if item.corroboration
                else "Weakly supported claim."
            )
            negatives.append(entry)

    return positives[:limit], negatives[:limit]


def _dimension_rationale(
    dimension: ScoreDimension,
    label: str,
    score: float,
    positives: list[dict[str, str]],
    negatives: list[dict[str, str]],
    count: int,
) -> str:
    band = band_for(_clamp100(score)).value
    lead = f"{label} scores {score:.0f}/100 ({band}) across {count} informing claim(s)."

    if dimension is ScoreDimension.TRANSLATIONAL_RISK:
        lead += (
            " Higher is better: this measures how well the evidence shown supports the "
            "clinical claim implied."
        )

    if negatives:
        lead += f" It is held down by {len(negatives)} weak or contradicted claim(s)."
    if positives:
        lead += f" It is supported by {len(positives)} well-corroborated claim(s)."
    if not positives and not negatives:
        lead += " No claim was decisive in either direction."
    return lead


# ------------------------------------------------------------ recommendation ---
def _recommend(
    overall: float,
    dimensions: list[DimensionScore],
    inputs: list[ScorecardInput],
    confidence: float,
) -> tuple[ICRecommendation, str]:
    contradicted = [
        i for i in inputs if i.score.scored and i.score.corroboration in ADVERSE_CORROBORATION
    ]
    contradicted_critical = [i for i in contradicted if i.scoring.is_thesis_critical]
    assessed = [d for d in dimensions if d.assessed]
    weak_dimensions = [d for d in assessed if (d.score or 0) < 40]

    # A contradicted thesis-critical claim dominates the recommendation
    # regardless of how well everything else scores.
    if contradicted_critical:
        return (
            ICRecommendation.SIGNIFICANT_CONCERNS,
            (
                f"{len(contradicted_critical)} thesis-critical claim(s) are contradicted by "
                "retrieved evidence. Resolve these before any further work: a thesis-critical "
                "claim that the public record disagrees with is the single most consequential "
                "finding this analysis can produce."
            ),
        )

    if overall >= 72 and confidence >= 0.55 and not weak_dimensions:
        return (
            ICRecommendation.ADVANCE,
            (
                f"The scientific case is well-evidenced across the assessed dimensions "
                f"({overall:.0f}/100) with no dimension below 40 and no contradicted claims. "
                "Proceed to commercial and financial diligence."
            ),
        )

    if overall >= 58:
        weak_names = ", ".join(d.label.lower() for d in weak_dimensions[:3])
        return (
            ICRecommendation.ADVANCE_WITH_CONDITIONS,
            (
                f"The scientific case holds at {overall:.0f}/100, but "
                + (
                    f"{weak_names} require(s) resolution before committing. "
                    if weak_names
                    else "assessment confidence is moderate. "
                )
                + "Advance conditional on the diligence items below."
            ),
        )

    if overall >= 40:
        return (
            ICRecommendation.FURTHER_DILIGENCE_REQUIRED,
            (
                f"At {overall:.0f}/100 the analysis is inconclusive rather than negative. "
                f"{sum(1 for i in inputs if i.score.scored and i.score.corroboration in (CorroborationStatus.INSUFFICIENT_EVIDENCE, CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED))} "
                "claim(s) could not be checked against public sources, so the primary "
                "constraint is information, not evidence against the company."
            ),
        )

    if contradicted:
        return (
            ICRecommendation.SIGNIFICANT_CONCERNS,
            (
                f"Overall score {overall:.0f}/100 with {len(contradicted)} contradicted claim(s). "
                "The evidence base does not currently support the scientific narrative."
            ),
        )

    return (
        ICRecommendation.FURTHER_DILIGENCE_REQUIRED,
        (
            f"Overall score {overall:.0f}/100. The dominant issue is thin evidence rather than "
            "adverse evidence; treat this as an information problem and obtain primary data "
            "before drawing a conclusion."
        ),
    )


def _clamp100(value: float) -> float:
    return max(0.0, min(100.0, value))
