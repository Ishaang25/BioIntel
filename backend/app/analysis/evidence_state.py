"""One evidence state per claim, and how much it licenses a conclusion.

The claim scorer already refuses to treat a null search as evidence against a
claim: an unchecked claim keeps its type-specific prior instead of falling.
That fix was correct and it stopped one level too early.

The scorecard then averaged those credibility scores into dimensions. A prior
is a *starting point for evidence*, not an assessment -- but a mechanism claim
sitting at its 0.35 prior contributes 35/100 to scientific validity, which
reads to every downstream consumer exactly like "weak science". Across the
benchmark decks the effect was systematic:

    Moderna    23 claims at insufficient_evidence, mean credibility 40.8
    Beam       37 claims at not_independently_verified, mean credibility 31.1
    Recursion  26 claims at plausible_unverified,  mean credibility 46.1

Nothing contradicted those claims. The scores are the priors, averaged, and
presented as findings. Worse, one gap was charged five times over, because
a claim informs several dimensions and each one averaged the same depressed
number independently.

This module separates the two things that were entangled in a single float:

**credibility** -- how believable the science is.
**informativeness** -- how much this claim's evidence licenses *any*
conclusion about a dimension.

A dimension then aggregates credibility weighted by informativeness, and
reports what it could not establish as reduced *confidence*. The uncertainty
is consumed once, where it belongs, instead of being subtracted repeatedly
from the thing it is not evidence about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analysis.claim_policy import ClaimPolicy, policy_for
from app.analysis.corroboration import CorroborationAssessment
from app.core.enums import (
    ClaimType,
    CorroborationStatus,
    EvidenceGrade,
    EvidenceState,
    RetrievalStatus,
    VerifiabilityClass,
)

__all__ = [
    "INFORMATION_SCALE",
    "INFORMATIVENESS",
    "NO_INFORMATION_ANCHOR",
    "ClaimEvidence",
    "derive_evidence",
    "evidence_state_for",
    "informed_aggregate",
    "retrieval_status_for",
]

#: The score an aggregate takes when nothing informative bears on it.
#:
#: Not 0 and not 50: a company whose claims we could not check is not thereby
#: average, it is *unassessed*, and the number has to sit where a reader will
#: not mistake it for a finding. 58 is the bottom of the "promising but
#: unproven" calibration band -- which is exactly what an unverified,
#: uncontradicted scientific claim is.
NO_INFORMATION_ANCHOR = 58.0

#: How quickly an aggregate earns the right to move away from the anchor as
#: informative evidence accumulates. Smaller = faster.
INFORMATION_SCALE = 0.45

#: How much a claim in each state licenses a conclusion about a dimension,
#: 0 (tells us nothing) to 1 (settles it).
#:
#: The two unverified states are deliberately low rather than zero: a claim we
#: could not check still carries a little information, because the company
#: chose to make it in a document it is legally accountable for. They are not
#: *negative* at any value -- a low weight moves a dimension toward "unknown",
#: never downward.
INFORMATIVENESS: dict[EvidenceState, float] = {
    EvidenceState.VERIFIED: 1.00,
    EvidenceState.CONTRADICTED: 1.00,
    EvidenceState.IMPLAUSIBLE: 1.00,
    EvidenceState.PARTIALLY_VERIFIED: 0.70,
    EvidenceState.PLAUSIBLE_UNVERIFIED: 0.22,
    EvidenceState.COMPANY_REPORTED: 0.15,
    EvidenceState.NOT_APPLICABLE: 0.00,
}

#: Corroboration outcome -> evidence state. A pure relabelling: the
#: corroboration model already draws the distinctions correctly, and this
#: collapses them to the vocabulary the scorecard reasons in.
_STATE_BY_STATUS: dict[CorroborationStatus, EvidenceState] = {
    CorroborationStatus.CORROBORATED: EvidenceState.VERIFIED,
    CorroborationStatus.PARTIALLY_CORROBORATED: EvidenceState.PARTIALLY_VERIFIED,
    CorroborationStatus.PLAUSIBLE_UNVERIFIED: EvidenceState.PLAUSIBLE_UNVERIFIED,
    CorroborationStatus.INSUFFICIENT_EVIDENCE: EvidenceState.PLAUSIBLE_UNVERIFIED,
    CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED: EvidenceState.PLAUSIBLE_UNVERIFIED,
    CorroborationStatus.CONTRADICTED: EvidenceState.CONTRADICTED,
    CorroborationStatus.DISPUTED: EvidenceState.CONTRADICTED,
    CorroborationStatus.NOT_ASSESSABLE: EvidenceState.NOT_APPLICABLE,
}


@dataclass(slots=True)
class ClaimEvidence:
    """The single evidence signal every downstream consumer reads."""

    claim_id: str
    state: EvidenceState
    #: How believable the science is, 0-100. Never reduced by a failed search.
    credibility: float
    #: How certain we are of that judgement, 0-1. This is where a failed
    #: search is allowed to show up.
    confidence: float
    #: How much this claim licenses a conclusion, 0-1.
    informativeness: float
    #: How the search went, independently of what it found.
    retrieval_status: RetrievalStatus
    #: Strongest grade of evidence that bore on the claim at all.
    evidence_level: EvidenceGrade | None = None
    #: True when the claim rests on data only the company holds. The correct
    #: response is an audit request, not a lower science score.
    requires_audit: bool = False
    #: True only when external evidence genuinely disagrees.
    contradicted: bool = False
    supporting_count: int = 0
    contradicting_count: int = 0
    #: Analyst-facing note explaining the state, never empty.
    note: str = ""

    @property
    def is_verified(self) -> bool:
        return self.state in (EvidenceState.VERIFIED, EvidenceState.PARTIALLY_VERIFIED)

    @property
    def is_information_gap(self) -> bool:
        """True when the limit is what we know, not what the evidence shows."""
        return self.state in (
            EvidenceState.PLAUSIBLE_UNVERIFIED,
            EvidenceState.COMPANY_REPORTED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_state": self.state.value,
            "credibility": round(self.credibility, 2),
            "confidence": round(self.confidence, 4),
            "informativeness": round(self.informativeness, 4),
            "retrieval_status": self.retrieval_status.value,
            "evidence_level": self.evidence_level.value if self.evidence_level else None,
            "requires_audit": self.requires_audit,
            "contradicted": self.contradicted,
            "supporting_count": self.supporting_count,
            "contradicting_count": self.contradicting_count,
            "note": self.note,
        }


def retrieval_status_for(
    corroboration: CorroborationAssessment | None, *, retrieval_errored: bool = False
) -> RetrievalStatus:
    """How the search went -- a fact about BioIntel, not about the company."""
    if retrieval_errored:
        return RetrievalStatus.ERROR
    if corroboration is None:
        return RetrievalStatus.NONE
    if corroboration.evidence_considered == 0:
        return RetrievalStatus.NONE
    if corroboration.supporting_count or corroboration.contradicting_count:
        return RetrievalStatus.FOUND
    return RetrievalStatus.PARTIAL


def evidence_state_for(
    corroboration: CorroborationAssessment | None, policy: ClaimPolicy
) -> EvidenceState:
    """Collapse a corroboration outcome into one evidence state."""
    if not policy.scorable:
        return EvidenceState.NOT_APPLICABLE
    if corroboration is None:
        return _unverified_state(policy)

    state = _STATE_BY_STATUS.get(corroboration.status, EvidenceState.PLAUSIBLE_UNVERIFIED)
    if state is EvidenceState.PLAUSIBLE_UNVERIFIED:
        # Distinguish "nobody has published this" from "nobody could publish
        # this": a proprietary assay is not weak science, it is unaudited.
        return _unverified_state(policy)
    return state


def _unverified_state(policy: ClaimPolicy) -> EvidenceState:
    if policy.verifiability is VerifiabilityClass.COMPANY_INTERNAL:
        return EvidenceState.COMPANY_REPORTED
    return EvidenceState.PLAUSIBLE_UNVERIFIED


def derive_evidence(
    *,
    claim_id: str,
    claim_type: ClaimType | str,
    credibility: float,
    confidence: float,
    corroboration: CorroborationAssessment | None,
    retrieval_errored: bool = False,
) -> ClaimEvidence:
    """Build the one evidence signal that the scorecard consumes."""
    policy = policy_for(claim_type)
    state = evidence_state_for(corroboration, policy)
    retrieval = retrieval_status_for(corroboration, retrieval_errored=retrieval_errored)

    informativeness = INFORMATIVENESS.get(state, 0.2)
    if corroboration is not None and corroboration.authoritative:
        # A regulator or registry settled it; that is as informative as we get.
        informativeness = max(informativeness, 0.9)

    return ClaimEvidence(
        claim_id=claim_id,
        state=state,
        credibility=credibility,
        confidence=confidence,
        informativeness=informativeness,
        retrieval_status=retrieval,
        evidence_level=corroboration.best_grade if corroboration else None,
        requires_audit=state is EvidenceState.COMPANY_REPORTED,
        contradicted=state is EvidenceState.CONTRADICTED,
        supporting_count=corroboration.supporting_count if corroboration else 0,
        contradicting_count=corroboration.contradicting_count if corroboration else 0,
        note=_note(state, policy, corroboration),
    )


def informed_aggregate(
    entries: list[tuple[float, float, bool]],
) -> tuple[float, float]:
    """Aggregate credibility with uncertainty propagated, not double-charged.

    ``entries`` are ``(credibility, weight, is_information_gap)`` triples.

    Used by both the run-level score and every scorecard dimension so the two
    cannot drift apart. Two rules, and they are the whole of the fix:

    1. A claim we could not check may *raise* an aggregate when the deck
       presents it well, but never lower one. Its credibility is a prior --
       a starting point for evidence, not an assessment -- and averaging a
       0.35 prior into a score is how "we found nothing" became "the science
       is weak".
    2. Missing information is not redistributed as a penalty. The aggregate is
       pulled toward :data:`NO_INFORMATION_ANCHOR`, the value that asserts
       nothing, in proportion to how little was established. The caller
       reports the shortfall as reduced confidence.

    Returns ``(score, information_ratio)``.
    """
    total_weight = sum(w for _, w, _ in entries)
    if total_weight <= 0:
        return NO_INFORMATION_ANCHOR, 0.0

    informed_weight = 0.0
    informed_credibility = 0.0
    for credibility, weight, is_gap in entries:
        if is_gap:
            share = weight * INFORMATIVENESS[EvidenceState.PLAUSIBLE_UNVERIFIED]
            credibility = max(credibility, NO_INFORMATION_ANCHOR)
        else:
            share = weight
        informed_weight += share
        informed_credibility += credibility * share

    information_ratio = min(1.0, informed_weight / total_weight)
    evidenced = (
        informed_credibility / informed_weight if informed_weight > 0 else NO_INFORMATION_ANCHOR
    )

    # Concave: the first genuinely verified claim tells you far more than the
    # fifth, so even modest corroboration may move an aggregate off neutral.
    blend = min(1.0, 1.0 - pow(2.718281828, -information_ratio / INFORMATION_SCALE))
    score = blend * evidenced + (1.0 - blend) * NO_INFORMATION_ANCHOR
    return score, information_ratio


def _note(
    state: EvidenceState, policy: ClaimPolicy, corroboration: CorroborationAssessment | None
) -> str:
    if state is EvidenceState.COMPANY_REPORTED:
        return (
            "This rests on data the company holds and has not published. It is a company "
            "assertion requiring audit, not a scientific weakness: request the underlying "
            "dataset. " + (policy.null_result_note or "")
        ).strip()
    if state is EvidenceState.PLAUSIBLE_UNVERIFIED:
        return (
            "No external evidence was found either way. This is an information gap, not a "
            "finding against the claim; it lowers assessment confidence rather than "
            "scientific credibility. " + (policy.null_result_note or "")
        ).strip()
    if corroboration is not None and corroboration.rationale:
        return corroboration.rationale
    return state.value.replace("_", " ")
