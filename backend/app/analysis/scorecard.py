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
from app.analysis.evidence_state import NO_INFORMATION_ANCHOR, informed_aggregate
from app.analysis.scoring import ClaimScore, ClaimScoringInput, band_for
from app.core.enums import (
    ADVERSE_CORROBORATION,
    BIOMEDICAL_ARCHETYPES,
    ClaimType,
    CompanyArchetype,
    ConfidenceLevel,
    CorroborationStatus,
    CredibilityBand,
    EvidenceState,
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
ARCHETYPE_WEIGHTS[CompanyArchetype.MEDICAL_DEVICE] = ARCHETYPE_WEIGHTS[
    CompanyArchetype.CLINICAL_STAGE_ASSET
]
ARCHETYPE_WEIGHTS[CompanyArchetype.UNKNOWN] = ARCHETYPE_WEIGHTS[
    CompanyArchetype.CLINICAL_STAGE_ASSET
]

#: For a company outside life sciences only the archetype-neutral axes carry
#: any meaning: what the company disclosed, how concentrated it is, and
#: whether it has executed. The biomedical axes are reported as inapplicable.
_COMMERCIAL_WEIGHTS: dict[ScoreDimension, float] = {
    ScoreDimension.EXECUTION_CREDIBILITY: 1.3,
    ScoreDimension.COMMERCIAL_READINESS: 1.3,
    ScoreDimension.DISCLOSURE_QUALITY: 1.1,
    ScoreDimension.PIPELINE_DIVERSIFICATION: 0.9,
    ScoreDimension.EVIDENCE_QUALITY: 0.7,
}
for _archetype in (
    CompanyArchetype.NON_BIOMEDICAL,
    CompanyArchetype.HEALTHCARE_SOFTWARE,
    CompanyArchetype.HEALTHCARE_SERVICES,
):
    ARCHETYPE_WEIGHTS[_archetype] = _COMMERCIAL_WEIGHTS


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
    #: Share of this dimension's claim weight that carried real information,
    #: 0-1. Low means "we could not establish this", which belongs in
    #: confidence -- not in the score.
    information_ratio: float = 0.0
    #: Claims resting on company-held data. These need an audit, not scepticism.
    claims_requiring_audit: int = 0
    #: False when the biomedical framework does not apply to this company.
    applicable: bool = True

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
                    "applicable": d.applicable,
                    "information_ratio": d.information_ratio,
                    "claims_requiring_audit": d.claims_requiring_audit,
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


#: Dimensions that only mean something for a company developing a medical
#: product. Applied to anything else they manufacture findings: a satellite
#: communications deck was scored 36.7 for "scientific validity" and 26.0 for
#: "commercial readiness" as a *biotech*, numbers that measure nothing and
#: read as a damning assessment.
BIOMEDICAL_ONLY_DIMENSIONS = frozenset(
    {
        ScoreDimension.SCIENTIFIC_VALIDITY,
        ScoreDimension.CLINICAL_MATURITY,
        ScoreDimension.REGULATORY_CONFIDENCE,
        ScoreDimension.TRANSLATIONAL_RISK,
        ScoreDimension.PLATFORM_STRENGTH,
    }
)

#: Claim types that only a life-sciences company makes. Their presence is the
#: positive signal that the biomedical framework applies at all.
_BIOMEDICAL_CLAIM_TYPES = frozenset(
    {
        ClaimType.CLINICAL_RESULT,
        ClaimType.PRECLINICAL_RESULT,
        ClaimType.PIPELINE_STAGE,
        ClaimType.REGULATORY_APPROVAL,
        ClaimType.REGULATORY_SUBMISSION,
        ClaimType.MECHANISM,
        ClaimType.BIOMARKER,
        ClaimType.SAFETY,
    }
)

#: Below this share of biomedical claims, the deck is not describing the
#: development of a medical product and must not be scored as though it were.
BIOMEDICAL_CLAIM_THRESHOLD = 0.12


def is_biomedical(inputs: list[ScorecardInput]) -> bool:
    """Whether the biomedical scoring framework applies to this company.

    Asks for positive evidence that the deck is about developing a medical
    product, rather than assuming it and scoring accordingly. A deck with no
    clinical, preclinical, regulatory, mechanistic or safety claims is not a
    biotech deck, however many numbers it contains.
    """
    scorable = [i for i in inputs if i.score.scored]
    if not scorable:
        return False
    biomedical = sum(1 for i in scorable if i.scoring.claim_type in _BIOMEDICAL_CLAIM_TYPES)
    return (biomedical / len(scorable)) >= BIOMEDICAL_CLAIM_THRESHOLD


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

    The first question is whether this is a life-sciences company at all.
    Answering it late -- or not at all -- is what let a satellite
    communications deck be scored on translational readiness.
    """
    if inputs and not is_biomedical(inputs):
        return CompanyArchetype.NON_BIOMEDICAL

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

    biomedical = resolved_archetype in BIOMEDICAL_ARCHETYPES

    dimensions = []
    for dimension in ScoreDimension:
        if not biomedical and dimension in BIOMEDICAL_ONLY_DIMENSIONS:
            dimensions.append(_not_applicable(dimension, resolved_archetype))
            continue
        dimensions.append(
            _score_dimension(
                dimension, inputs, resolved_archetype, pipeline_size, marketing_claim_ratio
            )
        )

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

    recommendation, rationale = _recommend(
        overall, dimensions, inputs, confidence, resolved_archetype
    )

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
            "biomedical_framework_applied": biomedical,
            "dimensions_not_applicable": [
                d.dimension.value for d in dimensions if not d.applicable
            ],
            # Reported alongside the score, never folded into it: how much of
            # the company's account we managed to check is a different fact
            # from how good that account is.
            "verification_coverage": _verification_coverage([i for i in inputs if i.score.scored]),
            "evidence_states": _state_distribution(inputs),
            "claims_requiring_audit": sum(1 for i in inputs if i.score.requires_audit),
            "methodology": (
                "Each dimension aggregates the claims that inform it, weighted by claim "
                "importance and by how much each claim's evidence licenses a conclusion. "
                "Unverified claims carry low weight and pull a dimension toward neutral "
                "rather than downward; the resulting shortfall is reported as reduced "
                "confidence, not as a lower score. Dimensions with no informing claims are "
                "reported as not assessed; dimensions that do not apply to the company's "
                "archetype are reported as not applicable."
            ),
        },
    )


def _state_distribution(inputs: list[ScorecardInput]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in inputs:
        key = item.score.evidence_state.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _not_applicable(dimension: ScoreDimension, archetype: CompanyArchetype) -> DimensionScore:
    """A biomedical axis on a company the framework does not fit.

    Reported as inapplicable rather than unassessed or zero. The distinction
    matters to a reader: "we could not measure this" invites diligence,
    "this question does not apply to this company" invites a different
    framework entirely.
    """
    label, question = DIMENSION_META[dimension]
    return DimensionScore(
        dimension=dimension,
        label=label,
        question=question,
        score=None,
        confidence=0.0,
        applicable=False,
        rationale=(
            f"Not applicable: this document describes a "
            f"{archetype.value.replace('_', ' ')} company, and {label.lower()} is a measure "
            "of biomedical product development. Scoring it here would produce a number "
            "that looks like a finding but measures nothing. Assess this company with a "
            "commercial diligence framework instead."
        ),
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

    score, information_ratio, confidence = _aggregate(contributions)

    if dimension is ScoreDimension.TRANSLATIONAL_RISK:
        score = _apply_translational_adjustment(score, contributions)
    if dimension is ScoreDimension.CLINICAL_MATURITY:
        score = _apply_maturity_adjustment(score, inputs)
    if dimension is ScoreDimension.PLATFORM_STRENGTH:
        score = _apply_platform_coherence(score, inputs, archetype)

    positives, negatives = _drivers(contributions)
    audit_items = [i for i, _ in contributions if i.score.requires_audit]

    return DimensionScore(
        dimension=dimension,
        label=label,
        question=question,
        score=round(_clamp100(score), 2),
        confidence=round(confidence, 4),
        rationale=_dimension_rationale(
            dimension,
            label,
            score,
            positives,
            negatives,
            len(contributions),
            information_ratio,
            len(audit_items),
        ),
        positive_drivers=positives,
        negative_drivers=negatives,
        claims_considered=len(contributions),
        information_ratio=round(information_ratio, 4),
        claims_requiring_audit=len(audit_items),
    )


def _aggregate(contributions: list[tuple[ScorecardInput, float]]) -> tuple[float, float, float]:
    """Aggregate claims into one dimension score, propagating uncertainty.

    The previous implementation took a plain weighted mean of credibility.
    That silently converted every information gap into a finding: an unchecked
    mechanism claim contributes its 35/100 prior, and a dimension built from
    such claims reads "weak science" when the honest answer is "not
    established either way". Because a claim informs several dimensions, the
    same gap was then charged once per dimension.

    Here each claim is weighted by how much it actually licenses a conclusion
    (:data:`~app.analysis.evidence_state.INFORMATIVENESS`). Whatever weight is
    missing is *not* redistributed as a penalty -- the score is pulled toward
    :data:`NO_INFORMATION_ANCHOR`, the value that asserts nothing, and the
    shortfall is reported as reduced confidence instead.

    Returns ``(score, information_ratio, confidence)``.
    """
    total_weight = sum(w for _, w in contributions)
    if total_weight <= 0:
        return NO_INFORMATION_ANCHOR, 0.0, 0.0

    score, information_ratio = informed_aggregate(
        [
            (
                item.score.credibility_score,
                weight,
                bool(item.score.evidence and item.score.evidence.is_information_gap),
            )
            for item, weight in contributions
        ]
    )

    # Confidence is the honest home for the missing information. A dimension
    # built entirely from unverified claims scores near neutral with low
    # confidence -- "we could not establish this" -- instead of scoring 40 and
    # implying we established something bad.
    claim_confidence = sum(i.score.confidence * w for i, w in contributions) / total_weight
    confidence = _clamp01(0.35 * claim_confidence + 0.65 * information_ratio)

    return score, information_ratio, confidence


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
        # Structural: computed from what the deck disclosed, not from evidence
        # retrieval, so there is no information gap to propagate here.
        information_ratio=1.0,
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
        # Structural: measured from the deck itself, not from retrieval.
        information_ratio=1.0,
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
            "evidence_state": item.score.evidence_state.value,
        }
        if item.score.corroboration in ADVERSE_CORROBORATION:
            entry["reason"] = "Retrieved evidence disagrees with this claim."
            negatives.append(entry)
        elif item.score.credibility_score >= 65 and item.score.informativeness >= 0.5:
            entry["reason"] = (
                item.corroboration.rationale[:200]
                if item.corroboration
                else "Well-supported claim."
            )
            positives.append(entry)
        elif item.score.credibility_score < 40 and item.score.informativeness >= 0.5:
            # Only a claim we actually *checked* may be listed as a negative
            # driver. Listing an unverified claim here is how an information
            # gap gets re-presented to the reader as an adverse finding.
            entry["reason"] = (
                item.corroboration.rationale[:200]
                if item.corroboration
                else "Checked against external evidence and poorly supported."
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
    information_ratio: float = 1.0,
    audit_count: int = 0,
) -> str:
    band = band_for(_clamp100(score)).value
    lead = f"{label} scores {score:.0f}/100 ({band}) across {count} informing claim(s)."

    if dimension is ScoreDimension.TRANSLATIONAL_RISK:
        lead += (
            " Higher is better: this measures how well the evidence shown supports the "
            "clinical claim implied."
        )

    if negatives:
        lead += f" It is held down by {len(negatives)} contradicted or weakly-evidenced claim(s)."
    if positives:
        lead += f" It is supported by {len(positives)} well-corroborated claim(s)."
    if not positives and not negatives:
        lead += " No claim was decisive in either direction."

    # State the epistemic position explicitly. A reader must be able to tell a
    # dimension we assessed as mediocre from one we could not assess.
    if information_ratio < 0.35:
        lead += (
            f" Only {information_ratio:.0%} of the claim weight here carried externally "
            "verifiable information, so this score is held near neutral and the "
            "uncertainty is reflected in its confidence rather than in the number. "
            "The constraint is what could be checked, not what was found."
        )
    if audit_count:
        lead += (
            f" {audit_count} claim(s) rest on company-held data and require audit rather "
            "than scientific challenge."
        )
    return lead


# ------------------------------------------------------------ recommendation ---
def _recommend(
    overall: float,
    dimensions: list[DimensionScore],
    inputs: list[ScorecardInput],
    confidence: float,
    archetype: CompanyArchetype = CompanyArchetype.UNKNOWN,
) -> tuple[ICRecommendation, str]:
    """Decide what to do next from the science, our certainty, and coverage.

    Not from the score alone. Keying off one number conflates two situations
    an investment committee treats completely differently:

        strong biology, little external verification  -> advance, with conditions
        strong verification, weak or adverse biology  -> significant concerns

    Both can produce the same middling number. Routing on the score alone sent
    the first to "significant concerns", which is the opposite of the correct
    advice: the response to an information gap is diligence, not rejection.
    """
    scored = [i for i in inputs if i.score.scored]
    contradicted = [i for i in scored if i.score.corroboration in ADVERSE_CORROBORATION]
    contradicted_critical = [i for i in contradicted if i.scoring.is_thesis_critical]
    implausible = [i for i in scored if i.score.evidence_state is EvidenceState.IMPLAUSIBLE]

    assessed = [d for d in dimensions if d.assessed]
    # A dimension is only "weak" if we actually established it is weak.
    weak_dimensions = [d for d in assessed if (d.score or 0) < 40 and d.information_ratio >= 0.35]
    verification_coverage = _verification_coverage(scored)
    audit_items = [i for i in scored if i.score.requires_audit]

    if not assessed:
        return (
            ICRecommendation.FURTHER_DILIGENCE_REQUIRED,
            (
                "No dimension could be assessed from this document. "
                + (
                    "The biomedical diligence framework does not apply to this company; "
                    "route it to commercial diligence."
                    if archetype not in BIOMEDICAL_ARCHETYPES
                    else "Obtain a document containing the company's scientific claims."
                )
            ),
        )

    # --- genuine adverse findings dominate, and only these ----------------
    if implausible:
        return (
            ICRecommendation.DO_NOT_ADVANCE,
            (
                f"{len(implausible)} claim(s) are inconsistent with established biology. "
                "This is a scientific objection, not an evidence gap, and it should be "
                "resolved with the company's scientific founders before any further work."
            ),
        )

    if contradicted_critical:
        # Proportionate, not absolute. A contradicted thesis-critical claim is
        # the most consequential finding this analysis produces, but "how much
        # of the thesis does it take down" is a different question from "does
        # it exist". One overstated development stage in a company with
        # approved products is a condition to clear, not a reason to stop; the
        # same finding in a company with nothing else verified is decisive.
        critical_total = max(1, sum(1 for i in scored if i.scoring.is_thesis_critical))
        share = len(contradicted_critical) / critical_total
        decisive = len(contradicted_critical) >= 2 or share > 0.34 or overall < 58

        if decisive:
            return (
                ICRecommendation.SIGNIFICANT_CONCERNS,
                (
                    f"{len(contradicted_critical)} of {critical_total} thesis-critical claim(s) "
                    "are contradicted by retrieved evidence. Resolve these before any further "
                    "work: a thesis-critical claim that the public record disagrees with is the "
                    "single most consequential finding this analysis can produce."
                ),
            )
        return (
            ICRecommendation.ADVANCE_WITH_CONDITIONS,
            (
                f"The scientific case is otherwise sound ({overall:.0f}/100), but "
                f"{len(contradicted_critical)} thesis-critical claim(s) are contradicted by the "
                "public record. This is a specific, checkable discrepancy rather than a broad "
                "evidence problem: make its resolution a gating condition, and confirm the "
                "company's position in writing before proceeding."
            ),
        )

    if archetype not in BIOMEDICAL_ARCHETYPES:
        return (
            ICRecommendation.FURTHER_DILIGENCE_REQUIRED,
            (
                f"This is a {archetype.value.replace('_', ' ')} company. The biomedical "
                "dimensions of this scorecard do not apply and were not scored; what remains "
                f"({overall:.0f}/100) measures disclosure and execution only. Assess this "
                "opportunity with a commercial diligence framework."
            ),
        )

    # --- the science is good; the question is how sure we are -------------
    strong_science = overall >= 70
    sound_science = overall >= 58

    if (
        strong_science
        and verification_coverage >= 0.4
        and confidence >= 0.6
        and not weak_dimensions
    ):
        return (
            ICRecommendation.ADVANCE,
            (
                f"The scientific case is both strong ({overall:.0f}/100) and independently "
                f"verified: {verification_coverage:.0%} of scored claims were corroborated "
                "against external sources, with no contradicted claims and no dimension "
                "established as weak. Proceed to commercial and financial diligence."
            ),
        )

    if strong_science:
        # Strong biology, thin verification. This is the case the old logic
        # got backwards.
        return (
            ICRecommendation.ADVANCE_WITH_CONDITIONS,
            (
                f"The science is strong ({overall:.0f}/100) but only "
                f"{verification_coverage:.0%} of claims could be independently verified, so "
                "assessment confidence is "
                f"{confidence_level(confidence).value}. The constraint is what we could check, "
                "not what we found: nothing retrieved contradicts the company's account. "
                "Advance conditional on the diligence items below"
                + (
                    f", including audit of {len(audit_items)} company-reported metric(s)."
                    if audit_items
                    else "."
                )
            ),
        )

    if sound_science:
        weak_names = ", ".join(d.label.lower() for d in weak_dimensions[:3])
        return (
            ICRecommendation.ADVANCE_WITH_CONDITIONS,
            (
                f"The scientific case holds at {overall:.0f}/100 with "
                f"{verification_coverage:.0%} of claims independently verified, but "
                + (
                    f"{weak_names} was/were assessed as weak on the evidence available and "
                    "require(s) resolution before committing. "
                    if weak_names
                    else f"assessment confidence is {confidence_level(confidence).value}. "
                )
                + "Advance conditional on the diligence items below."
            ),
        )

    if contradicted:
        return (
            ICRecommendation.SIGNIFICANT_CONCERNS,
            (
                f"Overall {overall:.0f}/100 with {len(contradicted)} claim(s) contradicted by "
                "retrieved evidence. Unlike an information gap, this is evidence against the "
                "company's account and does not resolve with more diligence."
            ),
        )

    return (
        ICRecommendation.FURTHER_DILIGENCE_REQUIRED,
        (
            f"At {overall:.0f}/100 the analysis is inconclusive rather than negative: "
            f"{verification_coverage:.0%} of scored claims were externally verified and none "
            "were contradicted. The dominant constraint is information, not adverse evidence. "
            "Obtain primary data before drawing a conclusion"
            + (
                f", starting with audit of {len(audit_items)} company-reported metric(s)."
                if audit_items
                else "."
            )
        ),
    )


def _verification_coverage(scored: list[ScorecardInput]) -> float:
    """Share of scored claims that external evidence actually spoke to.

    Reported separately from the score throughout, because it answers a
    different question: not "is the science good" but "how much of it did we
    manage to check".
    """
    if not scored:
        return 0.0
    verified = sum(1 for i in scored if i.score.evidence and i.score.evidence.is_verified)
    return round(verified / len(scored), 4)


def _clamp100(value: float) -> float:
    return max(0.0, min(100.0, value))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
