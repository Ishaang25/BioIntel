"""Credibility scoring.

Scores are computed **in code**, not by a language model.  The model supplies
per-item judgements (stance, relevance, strength); this module aggregates them
with fixed, inspectable arithmetic, and every score ships with the components
that produced it.

Rewritten after the Moderna review, which exposed a structural flaw: the old
model asked one question of every claim — "what experiment does the deck
describe?" — and scored zero for anything that described none.  An FDA
approval describes no experiment, so an approved product scored 27.5/100,
identical to a baseless slogan.

The model is now built on three principles.

**1. The claim's type decides how it is scored.**
    A regulatory approval, a mouse result and a revenue projection are
    epistemically different objects.  Each claim type carries its own prior
    and its own sensitivity to evidence (:mod:`app.analysis.claim_policy`).
    Statements that cannot be true or false today — guidance, plans, vision,
    marketing — are excluded from credibility entirely rather than scored as
    weak.

**2. Absence of evidence is never evidence against.**
    A null search result moves the score *toward the prior*, not down.  Only
    :data:`~app.core.enums.ADVERSE_CORROBORATION` statuses — contradicted and
    disputed — can push a claim below its prior.

**3. Evidence is weighted by what it can actually establish.**
    A pivotal trial in a top journal and a narrative review no longer carry
    comparable force (:mod:`app.evidence.grading`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.analysis.claim_policy import ClaimPolicy, policy_for
from app.analysis.corroboration import CorroborationAssessment
from app.analysis.evidence_state import (
    NO_INFORMATION_ANCHOR,
    ClaimEvidence,
    derive_evidence,
    informed_aggregate,
)
from app.core.enums import (
    ADVERSE_CORROBORATION,
    UNCHECKED_CORROBORATION,
    ClaimCategory,
    ClaimType,
    ConfidenceLevel,
    CorroborationStatus,
    CredibilityBand,
    EvidenceState,
    EvidenceTier,
    QuoteVerification,
    confidence_level,
)

# ---------------------------------------------------------------- constants ---
BAND_THRESHOLDS: tuple[tuple[float, CredibilityBand], ...] = (
    (75.0, CredibilityBand.STRONG),
    (60.0, CredibilityBand.MODERATE),
    (45.0, CredibilityBand.LIMITED),
    (30.0, CredibilityBand.WEAK),
    (0.0, CredibilityBand.UNSUPPORTED),
)

#: How each corroboration status moves a claim away from its prior, as a
#: signed fraction of the available headroom. Positive lifts toward 1.0,
#: negative pulls toward 0.0, zero leaves the claim at its prior.
#:
#: The two "we could not check" statuses are exactly 0.0. That single line is
#: the fix for the Moderna failure.
CORROBORATION_EFFECT: dict[CorroborationStatus, float] = {
    CorroborationStatus.CORROBORATED: 1.00,
    CorroborationStatus.PARTIALLY_CORROBORATED: 0.55,
    CorroborationStatus.PLAUSIBLE_UNVERIFIED: 0.12,
    CorroborationStatus.INSUFFICIENT_EVIDENCE: 0.00,
    CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED: 0.00,
    CorroborationStatus.DISPUTED: -0.45,
    CorroborationStatus.CONTRADICTED: -0.95,
    CorroborationStatus.NOT_ASSESSABLE: 0.00,
}

#: Rigour signals lift a claim modestly; their absence is reported as a risk
#: rather than scored as a large penalty, because a slide legitimately omits
#: statistics that the underlying dataset contains.
RIGOUR_BONUS = 0.12
#: Hedged wording is a real signal but a mild one.
HEDGING_PENALTY = 0.06
#: A quote that only fuzzy-matched the source is a provenance concern.
FUZZY_QUOTE_PENALTY = 0.04

#: Category weights for the run-level roll-up. Unchanged in spirit from the
#: previous model: a mechanism claim matters more than a market claim.
CATEGORY_WEIGHT: dict[ClaimCategory, float] = {
    ClaimCategory.CLINICAL_EFFICACY: 1.00,
    ClaimCategory.SAFETY: 0.90,
    ClaimCategory.PRECLINICAL_EFFICACY: 0.85,
    ClaimCategory.TARGET_VALIDATION: 0.85,
    ClaimCategory.MECHANISM: 0.80,
    ClaimCategory.BIOMARKER: 0.65,
    ClaimCategory.PLATFORM: 0.60,
    ClaimCategory.REGULATORY: 0.85,
    ClaimCategory.MANUFACTURING: 0.55,
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
    #: The kind of assertion this is. Drives the whole scoring model.
    claim_type: ClaimType = ClaimType.OTHER
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
    #: Corroboration outcome this score was built on.
    corroboration: CorroborationStatus
    supporting_count: int
    contradicting_count: int
    neutral_count: int
    evidence_quality: float
    consistency: float
    #: False when the claim is excluded from credibility scoring by type.
    scored: bool = True
    #: Plain-language explanation of the score, always populated.
    explanation: str = ""
    breakdown: dict[str, Any] = field(default_factory=dict)
    #: The single evidence signal downstream consumers read. Carries the
    #: state, how much it licenses a conclusion, and how the search itself
    #: went -- so no consumer has to re-derive uncertainty and charge for it
    #: a second time.
    evidence: ClaimEvidence | None = None

    @property
    def confidence_band(self) -> ConfidenceLevel:
        return confidence_level(self.confidence)

    @property
    def evidence_state(self) -> EvidenceState:
        return self.evidence.state if self.evidence else EvidenceState.NOT_APPLICABLE

    @property
    def informativeness(self) -> float:
        """How much this claim licenses a conclusion about a dimension."""
        return self.evidence.informativeness if self.evidence else 0.0

    @property
    def requires_audit(self) -> bool:
        return bool(self.evidence and self.evidence.requires_audit)


def score_claim(
    claim: ClaimScoringInput, corroboration: CorroborationAssessment | None
) -> ClaimScore:
    """Score one claim from its type, its rigour and what the evidence showed."""
    policy = policy_for(claim.claim_type)

    if not policy.scorable:
        return _unscored(claim, policy, corroboration)

    status = corroboration.status if corroboration else CorroborationStatus.INSUFFICIENT_EVIDENCE
    effect = CORROBORATION_EFFECT.get(status, 0.0)

    # ---- the prior: what a well-formed claim of this type is worth --------
    prior = policy.prior
    rigour = _rigour(claim)
    prior_adjustment = 0.0

    if claim.has_effect_size:
        # Only quantitative claims can be rigorous or not; a categorical
        # statement ("approved for 60+") is not improved by a p-value.
        prior_adjustment += RIGOUR_BONUS * (rigour - 0.5) * 2
    if claim.hedging_language:
        prior_adjustment -= HEDGING_PENALTY
    if claim.quote_verification is QuoteVerification.FUZZY:
        prior_adjustment -= FUZZY_QUOTE_PENALTY

    # A deck that states a strong evidence tier for its own claim earns a
    # small lift; stating none is not penalised, because most claim types
    # (approval, pipeline stage, partnership) never state one.
    tier_lift = _tier_lift(claim.claimed_tier, policy)
    base = _clamp(prior + prior_adjustment + tier_lift)

    # ---- move the base according to what the evidence showed -------------
    leverage = policy.evidence_leverage
    if effect >= 0:
        # Corroboration lifts toward 1.0, in proportion to headroom.
        score_unit = base + (1.0 - base) * leverage * effect
    else:
        # Contradiction pulls toward 0.0, in proportion to how far it can fall.
        # Note this is the *only* path that can reduce a claim below its prior.
        score_unit = base + base * leverage * effect

    score = 100.0 * _clamp(score_unit)

    # ---- how sure are we of this score ----------------------------------
    determination_confidence = corroboration.confidence if corroboration else 0.2
    confidence = _clamp(
        0.40 * determination_confidence
        + 0.25 * claim.extraction_confidence
        + 0.20 * (1.0 if claim.quote_verification is QuoteVerification.EXACT else 0.6)
        + 0.15 * (1.0 if (corroboration and corroboration.authoritative) else 0.4)
    )

    explanation = _explain(
        claim=claim,
        policy=policy,
        status=status,
        prior=prior,
        base=base,
        score=score,
        corroboration=corroboration,
    )

    evidence = derive_evidence(
        claim_id=claim.claim_id,
        claim_type=claim.claim_type,
        credibility=round(score, 2),
        confidence=round(confidence, 4),
        corroboration=corroboration,
    )
    if evidence.requires_audit:
        explanation += (
            " This claim rests on data only the company holds, so it is recorded as a "
            "company assertion requiring audit rather than as a scientific weakness."
        )

    return ClaimScore(
        claim_id=claim.claim_id,
        credibility_score=round(score, 2),
        band=band_for(score),
        confidence=round(confidence, 4),
        corroboration=status,
        supporting_count=corroboration.supporting_count if corroboration else 0,
        contradicting_count=corroboration.contradicting_count if corroboration else 0,
        neutral_count=corroboration.neutral_count if corroboration else 0,
        evidence_quality=round(corroboration.support_mass if corroboration else 0.0, 4),
        consistency=_consistency(corroboration),
        scored=True,
        explanation=explanation,
        evidence=evidence,
        breakdown={
            **evidence.to_dict(),
            "claim_type": _value(claim.claim_type),
            "verifiability": policy.verifiability.value,
            "prior": round(prior, 4),
            "prior_adjustment": round(prior_adjustment, 4),
            "tier_lift": round(tier_lift, 4),
            "base": round(base, 4),
            "corroboration_status": status.value,
            "corroboration_effect": effect,
            "evidence_leverage": leverage,
            "support_mass": round(corroboration.support_mass, 4) if corroboration else 0.0,
            "contradiction_mass": (
                round(corroboration.contradiction_mass, 4) if corroboration else 0.0
            ),
            "best_evidence_grade": (
                corroboration.best_grade.value
                if corroboration and corroboration.best_grade
                else None
            ),
            "authoritative_verification": bool(corroboration and corroboration.authoritative),
            "rigour": round(rigour, 4),
            "absence_penalised": False,
        },
    )


def _unscored(
    claim: ClaimScoringInput,
    policy: ClaimPolicy,
    corroboration: CorroborationAssessment | None,
) -> ClaimScore:
    """A claim excluded from credibility scoring by its type."""
    evidence = derive_evidence(
        claim_id=claim.claim_id,
        claim_type=claim.claim_type,
        credibility=0.0,
        confidence=1.0,
        corroboration=corroboration,
    )
    return ClaimScore(
        claim_id=claim.claim_id,
        credibility_score=0.0,
        band=CredibilityBand.UNSUPPORTED,
        confidence=1.0,
        corroboration=CorroborationStatus.NOT_ASSESSABLE,
        supporting_count=0,
        contradicting_count=0,
        neutral_count=0,
        evidence_quality=0.0,
        consistency=0.0,
        scored=False,
        evidence=evidence,
        explanation=(
            f"Excluded from credibility scoring: this is a "
            f"{_value(claim.claim_type).replace('_', ' ')} statement. "
            + (policy.null_result_note or "")
        ).strip(),
        breakdown={
            "claim_type": _value(claim.claim_type),
            "scorable": False,
            "reason": "not a factual claim about the present state of the world",
        },
    )


def _tier_lift(tier: EvidenceTier, policy: ClaimPolicy) -> float:
    """Small credit for the deck stating a strong evidence tier of its own.

    Deliberately small and never negative: most claim types have no natural
    tier to state, and the old model's habit of scoring ``none_stated`` as
    zero internal evidence is exactly what produced the Moderna result.
    """
    from app.core.enums import EVIDENCE_TIER_WEIGHT

    if tier is EvidenceTier.NONE_STATED:
        return 0.0
    weight = EVIDENCE_TIER_WEIGHT.get(tier, 0.0)
    headroom = max(0.0, 1.0 - policy.prior)
    return round(0.35 * headroom * weight, 4)


def _rigour(claim: ClaimScoringInput) -> float:
    signals = (
        claim.has_sample_size,
        claim.has_statistics,
        claim.has_comparator,
        claim.has_effect_size,
    )
    return sum(1.0 for s in signals if s) / len(signals)


def _consistency(corroboration: CorroborationAssessment | None) -> float:
    if corroboration is None:
        return 0.0
    total = (
        corroboration.supporting_count
        + corroboration.contradicting_count
        + corroboration.neutral_count
    )
    if total == 0:
        return 0.0
    dominant = max(corroboration.supporting_count, corroboration.contradicting_count)
    return round(dominant / total, 4)


def _explain(
    *,
    claim: ClaimScoringInput,
    policy: ClaimPolicy,
    status: CorroborationStatus,
    prior: float,
    base: float,
    score: float,
    corroboration: CorroborationAssessment | None,
) -> str:
    type_label = _value(claim.claim_type).replace("_", " ")
    parts = [
        f"Scored as a {type_label} claim, which starts from a prior of "
        f"{prior * 100:.0f}/100 because {_prior_reason(policy)}."
    ]
    if base != prior:
        direction = "raised" if base > prior else "lowered"
        parts.append(f"The claim's own presentation {direction} that to {base * 100:.0f}/100.")

    if status in UNCHECKED_CORROBORATION:
        parts.append(
            f"No independent confirmation was obtained, which leaves the score at its "
            f"prior rather than reducing it: {status.value.replace('_', ' ')} means the "
            "check could not be completed, not that the claim is doubtful."
        )
    elif status is CorroborationStatus.CORROBORATED:
        parts.append(f"Independent evidence corroborates it, raising the score to {score:.0f}/100.")
    elif status is CorroborationStatus.PARTIALLY_CORROBORATED:
        parts.append(
            f"Independent evidence supports it in part, raising the score to {score:.0f}/100."
        )
    elif status is CorroborationStatus.CONTRADICTED:
        parts.append(
            f"Retrieved evidence disagrees with it, which is the only condition that "
            f"reduces a score below its prior; the score falls to {score:.0f}/100."
        )
    elif status is CorroborationStatus.DISPUTED:
        parts.append(
            f"The evidence points both ways, reducing the score to {score:.0f}/100 pending "
            "expert adjudication."
        )
    else:
        parts.append(
            f"The claim is consistent with the retrieved literature without being "
            f"individually confirmed; the score is {score:.0f}/100."
        )

    if corroboration and corroboration.authoritative:
        parts.append("This determination came from an authoritative source, not inference.")
    return " ".join(parts)


def _prior_reason(policy: ClaimPolicy) -> str:
    return {
        ClaimType.REGULATORY_APPROVAL: (
            "approval status is a matter of public record that a company cannot "
            "misstate without legal exposure"
        ),
        ClaimType.REGULATORY_SUBMISSION: (
            "filings are company-disclosed and rarely publicly confirmable before action"
        ),
        ClaimType.PIPELINE_STAGE: "trial registration is mandatory and independently checkable",
        ClaimType.CLINICAL_RESULT: "clinical results must be earned from data, not asserted",
        ClaimType.PRECLINICAL_RESULT: (
            "preclinical data supporting a raise is normally unpublished and proprietary"
        ),
        ClaimType.MECHANISM: "mechanistic claims are the literature's strongest suit to judge",
        ClaimType.TRACK_RECORD: (
            "self-reported benchmarks depend entirely on company-chosen denominators"
        ),
        ClaimType.PARTNERSHIP: (
            "a named partner implies a counterparty completed its own diligence"
        ),
        ClaimType.COMPETITIVE_POSITION: (
            "companies systematically flatter themselves on differentiation"
        ),
    }.get(policy.claim_type, "of how claims of this kind can be checked")


def band_for(score: float) -> CredibilityBand:
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return CredibilityBand.UNSUPPORTED


# ------------------------------------------------------------- run level ---
@dataclass(slots=True)
class OverallScore:
    score: float
    band: CredibilityBand
    confidence: float
    breakdown: dict[str, Any] = field(default_factory=dict)

    @property
    def confidence_band(self) -> ConfidenceLevel:
        return confidence_level(self.confidence)


def score_run(
    claim_scores: list[ClaimScore],
    claim_inputs: dict[str, ClaimScoringInput],
    *,
    pages_analysed: int = 0,
    pages_total: int = 0,
    claims_needing_review: int = 0,
) -> OverallScore:
    """Aggregate scored claims into one credibility number.

    Only claims the scoring model accepts contribute.  Forward-looking and
    promotional statements are counted and reported, but a company is not
    marked down for having a strategy.
    """
    scorable = [s for s in claim_scores if s.scored]
    excluded = [s for s in claim_scores if not s.scored]

    if not scorable:
        return OverallScore(
            score=0.0,
            band=CredibilityBand.UNSUPPORTED,
            confidence=0.0,
            breakdown={
                "reason": (
                    "No scorable scientific claims were extracted. The document may consist "
                    "mainly of forward-looking or promotional statements."
                ),
                "claims_excluded_by_type": len(excluded),
            },
        )

    # Aggregated with the same uncertainty propagation the scorecard uses, via
    # the same primitive, so the headline number and the dimensions cannot
    # drift apart. A plain weighted mean of credibility here silently averaged
    # in the priors of every claim we could not check, which is what held an
    # approved commercial leader at 48.9/100.
    entries: list[tuple[float, float, bool]] = []
    for score in scorable:
        claim = claim_inputs.get(score.claim_id)
        if claim is None:
            continue
        weight = (
            CATEGORY_WEIGHT.get(claim.category, 0.3)
            * (0.35 + 0.65 * claim.importance)
            * (1.6 if claim.is_thesis_critical else 1.0)
        )
        is_gap = bool(score.evidence and score.evidence.is_information_gap)
        entries.append((score.credibility_score, weight, is_gap))

    base, information_ratio = informed_aggregate(entries)

    # --- penalties: only for genuine adverse findings ---------------------
    contradicted_critical = sum(
        1
        for s in scorable
        if s.corroboration in ADVERSE_CORROBORATION
        and (claim_inputs.get(s.claim_id) or _NULL_CLAIM).is_thesis_critical
    )
    contradicted_any = sum(1 for s in scorable if s.corroboration in ADVERSE_CORROBORATION)
    critical_total = max(1, sum(1 for c in claim_inputs.values() if c.is_thesis_critical))
    # Charged on the *share* of the thesis that is contradicted, not the count.
    #
    # Each contradicted claim has already been marked down heavily inside the
    # weighted mean -- the Moderna pipeline-stage discrepancy fell from a 0.70
    # prior to 33/100. A flat per-claim penalty here bills the same finding a
    # second time, and bills a thoroughly-extracted deck more than a sparse one
    # for identical underlying facts. What survives is the genuinely emergent
    # signal: a *pattern* of contradictions is worse than the sum of its parts.
    contradiction_penalty = min(
        12.0,
        12.0 * (contradicted_critical / critical_total) + 3.0 * (contradicted_any / len(scorable)),
    )

    coverage = (pages_analysed / pages_total) if pages_total else 1.0
    coverage_penalty = max(0.0, (1.0 - coverage) * 8.0)

    final = _clamp((base - contradiction_penalty - coverage_penalty) / 100.0) * 100.0

    unchecked = sum(1 for s in scorable if s.corroboration in UNCHECKED_CORROBORATION)
    corroborated = sum(
        1
        for s in scorable
        if s.corroboration
        in (CorroborationStatus.CORROBORATED, CorroborationStatus.PARTIALLY_CORROBORATED)
    )

    mean_confidence = _mean([s.confidence for s in scorable])
    review_ratio = claims_needing_review / max(1, len(scorable))
    # Verification coverage now drives assessment confidence, which is the
    # honest place for "we could not check much of this" to appear.
    verified_ratio = corroborated / max(1, len(scorable))
    overall_confidence = _clamp(
        0.45 * mean_confidence
        + 0.20 * coverage
        + 0.20 * verified_ratio
        + 0.15 * (1.0 - review_ratio)
    )

    return OverallScore(
        score=round(final, 2),
        band=band_for(final),
        confidence=round(overall_confidence, 4),
        breakdown={
            "weighted_base": round(base, 2),
            "claims_scored": len(scorable),
            "claims_excluded_by_type": len(excluded),
            "excluded_types": _excluded_type_counts(excluded, claim_inputs),
            "corroborated_claims": corroborated,
            "unchecked_claims": unchecked,
            "contradicted_claims": contradicted_any,
            "thesis_critical_claims": sum(1 for c in claim_inputs.values() if c.is_thesis_critical),
            "thesis_critical_contradicted": contradicted_critical,
            "information_ratio": round(information_ratio, 4),
            "no_information_anchor": NO_INFORMATION_ANCHOR,
            "penalties": {
                "contradiction": round(contradiction_penalty, 2),
                "page_coverage": round(coverage_penalty, 2),
                # Named explicitly so a reader can see it is gone on purpose.
                "absence_of_evidence": 0.0,
            },
            "page_coverage": round(coverage, 4),
            "claims_needing_review": claims_needing_review,
            "mean_claim_confidence": round(mean_confidence, 4),
            "verification_coverage": round(verified_ratio, 4),
            "band_thresholds": {band.value: threshold for threshold, band in BAND_THRESHOLDS},
            "methodology": (
                "Claims are scored from a type-specific prior, moved by corroboration "
                "outcome and evidence grade. Absence of corroboration leaves a claim at "
                "its prior; only contradicted or disputed evidence reduces a score."
            ),
        },
    )


def _excluded_type_counts(
    excluded: list[ClaimScore], claim_inputs: dict[str, ClaimScoringInput]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for score in excluded:
        claim = claim_inputs.get(score.claim_id)
        key = _value(claim.claim_type) if claim else "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


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


# ------------------------------------------------------------------ utils ---
def _value(enum_or_str: Any) -> str:
    return enum_or_str.value if hasattr(enum_or_str, "value") else str(enum_or_str)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _mean(values: list[Any]) -> float:
    numbers = [float(v) for v in values if isinstance(v, int | float)]
    return sum(numbers) / len(numbers) if numbers else 0.0


def stance_counts(adjudication: Any) -> dict[str, int]:
    """Backwards-compatible helper retained for the orchestrator."""
    from app.core.enums import Stance

    counts = dict.fromkeys((s.value for s in Stance), 0)
    if adjudication is None:
        return counts
    for evidence in adjudication.evidence:
        counts[evidence.stance.value] += 1
    return counts


__all__ = [
    "BAND_THRESHOLDS",
    "CATEGORY_WEIGHT",
    "CORROBORATION_EFFECT",
    "ClaimScore",
    "ClaimScoringInput",
    "OverallScore",
    "band_for",
    "math",
    "score_claim",
    "score_run",
    "stance_counts",
]
