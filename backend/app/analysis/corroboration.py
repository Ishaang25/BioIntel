"""Deciding what the evidence actually says about a claim.

Separated from scoring on purpose.  The old system collapsed two different
questions into one number:

1. *What did we find?*  — a factual question about the search.
2. *How credible is the claim?* — a judgement built on the answer to (1).

Conflating them is what let "we searched and found nothing" be scored the same
as "we searched and found disagreement".  This module answers only (1), and
produces a :class:`CorroborationStatus` that :mod:`app.analysis.scoring` then
converts into credibility.

The resolution order matters and is deliberate:

* an authoritative verification (FDA, registry) outranks literature, because
  it addresses the claim directly rather than by topical proximity;
* genuine contradiction outranks corroboration, because disconfirmation is
  more informative;
* a null result is resolved through the claim's own policy, so "no papers
  found" means something different for a preclinical result (expected) than
  for a Phase 3 efficacy claim (notable).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analysis.adjudicator import AdjudicatedEvidence, ClaimAdjudication
from app.analysis.claim_policy import ClaimPolicy, policy_for
from app.analysis.verification import VerificationResult
from app.core.enums import (
    EVIDENCE_GRADE_WEIGHT,
    ClaimType,
    ConfidenceLevel,
    CorroborationStatus,
    EvidenceGrade,
    Stance,
    confidence_level,
)
from app.evidence.grading import grade_record

#: Evidence below this relevance is treated as off-topic noise, whichever
#: stance the model assigned it.
MIN_RELEVANCE = 0.25
#: Corroborating mass at which a claim counts as properly corroborated.
CORROBORATION_THRESHOLD = 0.55
#: ...and at which it counts as partially corroborated.
PARTIAL_THRESHOLD = 0.22
#: Contradicting mass at which a claim counts as contradicted.
CONTRADICTION_THRESHOLD = 0.30
#: Ratio within which support and contradiction are treated as disputed
#: rather than one side winning.
DISPUTE_RATIO = 1.5


@dataclass(slots=True)
class CorroborationAssessment:
    """What the evidence establishes about one claim, and how sure we are."""

    claim_id: str
    status: CorroborationStatus
    #: 0-1 weight of evidence that agrees, graded by the hierarchy.
    support_mass: float = 0.0
    #: 0-1 weight of evidence that disagrees.
    contradiction_mass: float = 0.0
    #: The strongest grade of evidence that bears on the claim at all.
    best_grade: EvidenceGrade | None = None
    #: How much to trust this determination, 0-1.
    confidence: float = 0.0
    #: Whether an authoritative source (regulator/registry) settled it.
    authoritative: bool = False
    #: Analyst-facing explanation of why this status, never empty.
    rationale: str = ""
    supporting_count: int = 0
    contradicting_count: int = 0
    neutral_count: int = 0
    evidence_considered: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def confidence_band(self) -> ConfidenceLevel:
        return confidence_level(self.confidence)

    @property
    def is_adverse(self) -> bool:
        return self.status in (
            CorroborationStatus.CONTRADICTED,
            CorroborationStatus.DISPUTED,
        )


def assess_corroboration(
    *,
    claim_id: str,
    claim_type: ClaimType | str,
    adjudication: ClaimAdjudication | None,
    verification: VerificationResult | None = None,
) -> CorroborationAssessment:
    """Determine what the retrieved evidence establishes about a claim."""
    policy = policy_for(claim_type)

    # Claims that are not statements about the present world are reported but
    # never corroborated: a plan or a slogan has no truth value to check.
    if not policy.scorable:
        return CorroborationAssessment(
            claim_id=claim_id,
            status=CorroborationStatus.NOT_ASSESSABLE,
            confidence=1.0,
            rationale=(
                policy.null_result_note
                or "This statement is not a factual claim about the present and is "
                "excluded from credibility scoring."
            ),
        )

    # An authoritative source addressed the claim directly. Nothing the
    # literature says by topical proximity should override it.
    if verification is not None and verification.is_decisive:
        return _from_verification(claim_id, policy, verification, adjudication)

    relevant = _relevant_evidence(adjudication)
    if not relevant:
        return _null_result(claim_id, policy, adjudication, verification)

    support_mass, contradiction_mass = _weigh(relevant)
    best = max(
        (grade_record(e.record).grade for e in relevant),
        key=lambda g: EVIDENCE_GRADE_WEIGHT.get(g, 0.0),
    )
    counts = _counts(relevant)

    status, rationale, confidence = _classify(
        policy=policy,
        support_mass=support_mass,
        contradiction_mass=contradiction_mass,
        best=best,
        counts=counts,
    )

    assessment = CorroborationAssessment(
        claim_id=claim_id,
        status=status,
        support_mass=round(support_mass, 4),
        contradiction_mass=round(contradiction_mass, 4),
        best_grade=best,
        confidence=round(confidence, 4),
        rationale=rationale,
        supporting_count=counts["supports"],
        contradicting_count=counts["contradicts"],
        neutral_count=counts["neutral"],
        evidence_considered=len(relevant),
    )

    if verification is not None and not verification.is_decisive:
        assessment.notes.append(verification.detail)
    return assessment


# ------------------------------------------------------------- resolution ---
def _from_verification(
    claim_id: str,
    policy: ClaimPolicy,
    verification: VerificationResult,
    adjudication: ClaimAdjudication | None,
) -> CorroborationAssessment:
    relevant = _relevant_evidence(adjudication)
    support_mass, contradiction_mass = _weigh(relevant)
    counts = _counts(relevant)

    status = verification.corroboration
    # A registry that positively disagrees is the strongest adverse signal we
    # can produce, and outranks any amount of topical literature support.
    return CorroborationAssessment(
        claim_id=claim_id,
        status=status,
        support_mass=round(support_mass, 4),
        contradiction_mass=round(contradiction_mass, 4),
        best_grade=(
            EvidenceGrade.REGULATORY_APPROVAL
            if verification.source == "openfda"
            else EvidenceGrade.REGISTRY_RECORD
        ),
        confidence=max(verification.confidence, 0.6),
        authoritative=True,
        rationale=verification.detail,
        supporting_count=counts["supports"],
        contradicting_count=counts["contradicts"],
        neutral_count=counts["neutral"],
        evidence_considered=len(relevant),
        notes=[f"Verified against {verification.source}."],
    )


def _null_result(
    claim_id: str,
    policy: ClaimPolicy,
    adjudication: ClaimAdjudication | None,
    verification: VerificationResult | None,
) -> CorroborationAssessment:
    """No on-topic evidence. What that means depends on the claim type."""
    searched = adjudication is not None and bool(adjudication.evidence)
    status = policy.null_result_status

    if verification is not None:
        # Verification was attempted and could not settle it: that is the more
        # informative explanation, so prefer its wording.
        status = verification.corroboration
        rationale = verification.detail
    elif searched:
        rationale = (
            f"{len(adjudication.evidence)} record(s) were retrieved but none were "
            "sufficiently on-topic to bear on this claim. "
        ) + (policy.null_result_note or "")
    else:
        rationale = ("No external records were retrieved for this claim. ") + (
            policy.null_result_note or ""
        )

    rationale = rationale.strip() + (
        " This is a statement about the search, not evidence that the claim is false."
        if status
        in (
            CorroborationStatus.INSUFFICIENT_EVIDENCE,
            CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
        )
        else ""
    )

    return CorroborationAssessment(
        claim_id=claim_id,
        status=status,
        # Confidence here is confidence in the *determination*, and we are
        # reasonably sure we found nothing; it is not confidence in the claim.
        confidence=0.35 if searched else 0.2,
        rationale=rationale,
        neutral_count=len(adjudication.evidence) if adjudication else 0,
        evidence_considered=len(adjudication.evidence) if adjudication else 0,
        notes=[verification.detail] if verification else [],
    )


def _classify(
    *,
    policy: ClaimPolicy,
    support_mass: float,
    contradiction_mass: float,
    best: EvidenceGrade,
    counts: dict[str, int],
) -> tuple[CorroborationStatus, str, float]:
    grade_label = best.value.replace("_", " ")
    total_mass = support_mass + contradiction_mass

    # --- genuine disagreement --------------------------------------------
    if contradiction_mass >= CONTRADICTION_THRESHOLD:
        if support_mass > 0 and (
            max(support_mass, contradiction_mass) / max(1e-6, min(support_mass, contradiction_mass))
            < DISPUTE_RATIO
        ):
            return (
                CorroborationStatus.DISPUTED,
                (
                    f"The evidence points both ways: {counts['supports']} record(s) agree and "
                    f"{counts['contradicts']} disagree, at comparable weight "
                    f"(strongest evidence: {grade_label}). This is a genuine scientific "
                    "disagreement rather than a gap, and warrants expert adjudication."
                ),
                min(0.85, 0.45 + total_mass * 0.4),
            )
        return (
            CorroborationStatus.CONTRADICTED,
            (
                f"{counts['contradicts']} retrieved record(s) disagree with this claim and "
                f"outweigh what supports it (strongest evidence: {grade_label}). This is "
                "evidence against the claim, not merely an absence of support."
            ),
            min(0.9, 0.5 + contradiction_mass * 0.45),
        )

    # --- corroboration ----------------------------------------------------
    corroborating = best in policy.corroborating_grades

    if support_mass >= CORROBORATION_THRESHOLD and corroborating:
        return (
            CorroborationStatus.CORROBORATED,
            (
                f"{counts['supports']} independent record(s) support this claim, the strongest "
                f"being a {grade_label}, which is a grade of evidence capable of corroborating "
                "a claim of this kind."
            ),
            min(0.92, 0.5 + support_mass * 0.45),
        )

    if support_mass >= PARTIAL_THRESHOLD:
        if not corroborating:
            return (
                CorroborationStatus.PLAUSIBLE_UNVERIFIED,
                (
                    f"{counts['supports']} record(s) are consistent with this claim, but the "
                    f"strongest available evidence is a {grade_label}, which cannot corroborate "
                    "a claim of this kind on its own. The claim is plausible within what the "
                    "literature establishes, but not independently confirmed."
                ),
                min(0.6, 0.3 + support_mass * 0.35),
            )
        return (
            CorroborationStatus.PARTIALLY_CORROBORATED,
            (
                f"{counts['supports']} record(s) support part of this claim (strongest evidence: "
                f"{grade_label}), but the corroboration is incomplete — typically the direction "
                "of effect is supported while the specific magnitude, population or label "
                "detail is not."
            ),
            min(0.75, 0.35 + support_mass * 0.45),
        )

    # --- on-topic but uninformative ---------------------------------------
    return (
        CorroborationStatus.PLAUSIBLE_UNVERIFIED,
        (
            f"{counts['neutral']} on-topic record(s) were found but none bear decisively on the "
            f"claim (strongest evidence: {grade_label}). The claim sits within established "
            "science for this area without being individually confirmed."
        ),
        0.4,
    )


# ---------------------------------------------------------------- weighing ---
def _relevant_evidence(adjudication: ClaimAdjudication | None) -> list[AdjudicatedEvidence]:
    if adjudication is None:
        return []
    return [
        e
        for e in adjudication.evidence
        if e.stance is not Stance.UNRELATED and e.relevance >= MIN_RELEVANCE
    ]


def _weigh(evidence: list[AdjudicatedEvidence]) -> tuple[float, float]:
    """Support and contradiction mass, graded by the evidence hierarchy.

    Each record contributes ``relevance x model strength x hierarchy weight``.
    The hierarchy weight is what stops a narrative review from carrying the
    same force as a pivotal trial.
    """
    support = 0.0
    contradiction = 0.0
    for item in evidence:
        graded = grade_record(item.record)
        mass = item.relevance * item.strength * graded.weight
        if item.stance is Stance.SUPPORTS:
            support += mass
        elif item.stance is Stance.CONTRADICTS:
            contradiction += mass
        elif item.stance is Stance.MIXED:
            support += mass * 0.4
            contradiction += mass * 0.4
    # Saturating: the tenth confirmatory review adds almost nothing.
    return _saturate(support), _saturate(contradiction)


def _saturate(mass: float, scale: float = 1.1) -> float:
    """Diminishing returns so volume cannot substitute for quality."""
    if mass <= 0:
        return 0.0
    return round(1.0 - pow(2.718281828, -mass / scale), 4)


def _counts(evidence: list[AdjudicatedEvidence]) -> dict[str, int]:
    counts = {"supports": 0, "contradicts": 0, "neutral": 0, "mixed": 0}
    for item in evidence:
        if item.stance is Stance.SUPPORTS:
            counts["supports"] += 1
        elif item.stance is Stance.CONTRADICTS:
            counts["contradicts"] += 1
        elif item.stance is Stance.MIXED:
            counts["mixed"] += 1
        else:
            counts["neutral"] += 1
    return counts


def status_distribution(
    assessments: list[CorroborationAssessment],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for assessment in assessments:
        counts[assessment.status.value] = counts.get(assessment.status.value, 0) + 1
    return counts
