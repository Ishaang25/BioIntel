"""Explanations computed from the evidence graph, not written by the model.

A score with no explanation is an assertion, and an explanation written by a
model *about* a score it did not compute is worse: it drifts. "Scientific
validity 58" tells a reader nothing, and a paragraph of prose invented to
justify 58 tells them something possibly untrue.

Everything here is derived arithmetic over the same evidence states the
scoring engine consumed, so a driver bullet cannot contradict the number it
explains. The model's job in the memo is analysis and prose; the job of
saying *which findings produced which number* belongs to code.

Three things are produced:

* **drivers** -- why a dimension scored what it did, as countable findings;
* **confidence reasons** -- why we are or are not sure, which is a different
  question from whether the science is good;
* **recommendation drivers** -- the evidence states that decided the verdict,
  so the reader can audit the reasoning rather than trust it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis.questions import Question
from app.core.enums import (
    BIOMEDICAL_ARCHETYPES,
    EvidenceState,
    QuestionPriority,
    RetrievalStatus,
)

__all__ = [
    "DimensionNarrative",
    "confidence_reasons",
    "dimension_narratives",
    "evidence_ledger",
    "rank_questions",
    "recommendation_drivers",
]

#: Below this share of externally verified claims, verification coverage is
#: itself the headline finding about a dimension.
THIN_COVERAGE = 0.35

#: How many diligence questions an IC will actually action.
TOP_QUESTIONS = 5


@dataclass(slots=True)
class DimensionNarrative:
    """Why one dimension scored what it did."""

    dimension: str
    label: str
    score: float | None
    band: str | None
    confidence: float
    confidence_band: str
    #: Countable findings, most decisive first. Rendered as bullets.
    drivers: list[str] = field(default_factory=list)
    #: Why confidence is what it is, separately from the score.
    confidence_reasons: list[str] = field(default_factory=list)
    #: What would move this dimension, if anything cheaply would.
    what_would_move_it: str = ""
    applicable: bool = True
    assessed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "label": self.label,
            "score": self.score,
            "band": self.band,
            "confidence": self.confidence,
            "confidence_band": self.confidence_band,
            "drivers": self.drivers,
            "confidence_reasons": self.confidence_reasons,
            "what_would_move_it": self.what_would_move_it,
            "applicable": self.applicable,
            "assessed": self.assessed,
        }


def _states(claims: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in claims:
        key = str(claim.get("evidence_state") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def evidence_ledger(claim_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """The counted facts every explanation in the memo is built from.

    One pass over the claims, so the executive summary, the dimension drivers
    and the recommendation block are all quoting the same arithmetic.
    """
    scored = [c for c in claim_summaries if c.get("credibility_score") is not None]
    thesis = [c for c in scored if c.get("is_thesis_critical")]
    states = _states(scored)

    verified = states.get(EvidenceState.VERIFIED.value, 0)
    partial = states.get(EvidenceState.PARTIALLY_VERIFIED.value, 0)
    unverified = states.get(EvidenceState.PLAUSIBLE_UNVERIFIED.value, 0)
    company = states.get(EvidenceState.COMPANY_REPORTED.value, 0)
    contradicted = states.get(EvidenceState.CONTRADICTED.value, 0)

    return {
        "claims_scored": len(scored),
        "thesis_critical": len(thesis),
        "verified": verified,
        "partially_verified": partial,
        "unverified": unverified,
        "company_reported": company,
        "contradicted": contradicted,
        "requires_audit": sum(1 for c in scored if c.get("requires_audit")),
        "thesis_verified": sum(
            1
            for c in thesis
            if c.get("evidence_state")
            in (EvidenceState.VERIFIED.value, EvidenceState.PARTIALLY_VERIFIED.value)
        ),
        "thesis_unverified": sum(
            1
            for c in thesis
            if c.get("evidence_state")
            in (EvidenceState.PLAUSIBLE_UNVERIFIED.value, EvidenceState.COMPANY_REPORTED.value)
        ),
        "thesis_contradicted": sum(
            1 for c in thesis if c.get("evidence_state") == EvidenceState.CONTRADICTED.value
        ),
        "verification_coverage": round((verified + partial) / len(scored), 4) if scored else 0.0,
        "retrieval_none": sum(
            1 for c in scored if c.get("retrieval_status") == RetrievalStatus.NONE.value
        ),
        "retrieval_error": sum(
            1 for c in scored if c.get("retrieval_status") == RetrievalStatus.ERROR.value
        ),
        "by_state": states,
    }


def dimension_narratives(
    scorecard: Any, claim_summaries: list[dict[str, Any]]
) -> list[DimensionNarrative]:
    """Turn each computed dimension into countable reasons for its score."""
    if scorecard is None:
        return []

    by_claim = {c.get("claim_id"): c for c in claim_summaries}
    out: list[DimensionNarrative] = []

    for dimension in scorecard.dimensions:
        narrative = DimensionNarrative(
            dimension=dimension.dimension.value,
            label=dimension.label,
            score=dimension.score,
            band=dimension.band.value if dimension.band else None,
            confidence=round(dimension.confidence, 4),
            confidence_band=dimension.confidence_band.value,
            applicable=dimension.applicable,
            assessed=dimension.assessed,
        )

        if not dimension.applicable:
            narrative.drivers = [
                "This axis measures biomedical product development and does not apply to "
                "this company."
            ]
            out.append(narrative)
            continue

        if not dimension.assessed:
            narrative.drivers = [
                "No extracted claim bears on this dimension — a gap in the deck, not an "
                "adverse finding."
            ]
            narrative.what_would_move_it = (
                "Any disclosure that speaks to this question would allow it to be scored."
            )
            out.append(narrative)
            continue

        narrative.drivers = _drivers_for(dimension, by_claim)
        narrative.confidence_reasons = _dimension_confidence_reasons(dimension)
        narrative.what_would_move_it = _what_would_move(dimension)
        out.append(narrative)

    return out


#: Dimensions computed from the deck's own structure rather than from
#: retrieved evidence. Their drivers come from their own rationale, because
#: talking about "corroborated claims" for a disclosure measure is a category
#: error -- disclosure quality asks whether an analyst *could* check the deck,
#: not what checking it found.
_STRUCTURAL_DIMENSIONS = {"pipeline_diversification", "disclosure_quality"}


def _drivers_for(dimension: Any, by_claim: dict[Any, dict[str, Any]]) -> list[str]:
    """Countable findings behind one dimension's score, most decisive first."""
    if dimension.dimension.value in _STRUCTURAL_DIMENSIONS:
        return _structural_drivers(dimension)

    drivers: list[str] = []

    verified = 0
    unverified = 0
    contradicted = 0
    audit = 0
    for driver in [*dimension.positive_drivers, *dimension.negative_drivers]:
        claim = by_claim.get(driver.get("claim_id"))
        state = (claim or {}).get("evidence_state") or driver.get("evidence_state")
        if state == EvidenceState.CONTRADICTED.value:
            contradicted += 1
        elif state in (EvidenceState.VERIFIED.value, EvidenceState.PARTIALLY_VERIFIED.value):
            verified += 1
        elif state == EvidenceState.COMPANY_REPORTED.value:
            audit += 1
        elif state == EvidenceState.PLAUSIBLE_UNVERIFIED.value:
            unverified += 1

    considered = dimension.claims_considered

    # Contradictions first: they are the only findings that lower a score.
    if contradicted:
        drivers.append(
            f"{contradicted} claim(s) contradicted by retrieved evidence — the only finding "
            "type that pushes this score down"
        )
    if verified:
        drivers.append(f"{verified} claim(s) corroborated against external sources")
    if unverified:
        drivers.append(
            f"{unverified} claim(s) consistent with the literature but not individually "
            "confirmed — neutral, not negative"
        )
    if dimension.claims_requiring_audit:
        drivers.append(
            f"{dimension.claims_requiring_audit} claim(s) rest on company-held data and "
            "require audit rather than scientific challenge"
        )
    if not contradicted:
        drivers.append("no biological or evidential contradictions found")

    if dimension.information_ratio < THIN_COVERAGE:
        drivers.append(
            f"only {dimension.information_ratio:.0%} of the informing claim weight could be "
            "externally checked, so the score is held near neutral by design"
        )

    drivers.append(f"{considered} claim(s) informed this dimension")
    return drivers


def _structural_drivers(dimension: Any) -> list[str]:
    """Drivers for a dimension measured from the deck, not from evidence."""
    drivers = [
        driver.get("reason", "")
        for driver in [*dimension.negative_drivers, *dimension.positive_drivers]
        if driver.get("reason")
    ]
    if not drivers:
        drivers.append(dimension.rationale.split(".")[0].strip() + ".")
    drivers.append(
        "Measured from what the deck discloses, not from external evidence — this axis is "
        "unaffected by verification coverage."
    )
    return drivers


def _dimension_confidence_reasons(dimension: Any) -> list[str]:
    if dimension.dimension.value in _STRUCTURAL_DIMENSIONS:
        return [
            "Measured directly from the deck's contents, so this dimension does not depend "
            "on what external retrieval could find."
        ]

    """Why we are, or are not, sure about this dimension.

    Never about whether the score is good -- only about how much we know.
    """
    reasons: list[str] = []
    if dimension.information_ratio < THIN_COVERAGE:
        reasons.append(
            f"Low verification coverage: {dimension.information_ratio:.0%} of the claim "
            "weight informing this dimension was externally checkable."
        )
    if dimension.claims_requiring_audit:
        reasons.append(
            f"{dimension.claims_requiring_audit} informing claim(s) rest on proprietary "
            "company data that no public source can confirm."
        )
    if dimension.claims_considered <= 2:
        reasons.append(
            f"Only {dimension.claims_considered} claim(s) bear on this dimension, so it rests "
            "on a narrow base."
        )
    if not reasons:
        reasons.append(
            "Evidence coverage across the informing claims was sufficient to assess this "
            "dimension directly."
        )
    return reasons


def _what_would_move(dimension: Any) -> str:
    if dimension.dimension.value == "disclosure_quality":
        return (
            "Sample sizes, comparators and statistics attached to the quantitative claims "
            "would move this — it measures checkability, not the quality of the science."
        )
    if dimension.dimension.value == "pipeline_diversification":
        return "Only additional disclosed programmes would move this."
    if dimension.information_ratio < THIN_COVERAGE:
        return (
            "Primary data or an authoritative record for the unverified claims would move "
            "this dimension more than any additional company narrative."
        )
    if dimension.negative_drivers:
        return "Resolving the contradicted claims above is the shortest path to moving this."
    return "Further corroboration of the claims already verified would consolidate it."


def confidence_reasons(
    ledger: dict[str, Any], scorecard: Any, warnings: list[str] | None = None
) -> list[str]:
    """Why overall assessment confidence is what it is.

    Deliberately separate from credibility. Low confidence means we could not
    check enough to be sure, which is a statement about the search and the
    sources -- never about the quality of the science.
    """
    reasons: list[str] = []
    coverage = ledger.get("verification_coverage", 0.0)

    if coverage < THIN_COVERAGE:
        reasons.append(
            f"Low verification coverage — {coverage:.0%} of scored claims were confirmed "
            f"against an external source ({ledger['verified'] + ledger['partially_verified']} "
            f"of {ledger['claims_scored']})."
        )
    if ledger.get("company_reported"):
        reasons.append(
            f"Company data proprietary — {ledger['company_reported']} claim(s) rest on "
            "internal datasets that no public source can confirm; these require audit."
        )
    if ledger.get("retrieval_none"):
        reasons.append(
            f"Primary datasets unavailable — literature retrieval returned nothing on point "
            f"for {ledger['retrieval_none']} claim(s)."
        )
    if ledger.get("retrieval_error"):
        reasons.append(
            f"Source unreachable — the evidence search failed for "
            f"{ledger['retrieval_error']} claim(s), so those gaps are ours, not the company's."
        )
    if scorecard is not None:
        unassessed = [d for d in scorecard.dimensions if d.applicable and not d.assessed]
        if unassessed:
            reasons.append(
                f"{len(unassessed)} scorecard dimension(s) had no informing claims: "
                + ", ".join(d.label.lower() for d in unassessed[:4])
                + "."
            )
    for warning in warnings or []:
        if "registry" in warning.lower() or "could not be" in warning.lower():
            reasons.append(warning)

    if not reasons:
        reasons.append(
            "Verification coverage was sufficient across the thesis-critical claims for the "
            "assessment to stand on retrieved evidence rather than assertion."
        )
    return reasons[:6]


def recommendation_drivers(
    scorecard: Any, ledger: dict[str, Any], overall_score: float
) -> list[str]:
    """The evidence states that produced the recommendation.

    Traceability, not justification: a reader should be able to check the
    verdict against these lines without rereading the memo.
    """
    if scorecard is None:
        return []

    drivers: list[str] = []
    coverage = ledger.get("verification_coverage", 0.0)

    if scorecard.archetype not in BIOMEDICAL_ARCHETYPES:
        drivers.append(
            f"company archetype is {scorecard.archetype.value.replace('_', ' ')} — the "
            "biomedical dimensions do not apply and were not scored"
        )
        return drivers

    band = scorecard.overall_band.value.replace("_", " ")
    drivers.append(f"science scores {scorecard.overall_score:.0f}/100 ({band})")
    drivers.append(
        f"verification coverage {coverage:.0%} "
        f"({ledger['verified'] + ledger['partially_verified']} of {ledger['claims_scored']} "
        "scored claims confirmed externally)"
    )

    if ledger.get("thesis_contradicted"):
        drivers.append(
            f"{ledger['thesis_contradicted']} unresolved contradiction(s) among "
            f"{ledger['thesis_critical']} thesis-critical claim(s)"
        )
    elif ledger.get("contradicted"):
        drivers.append(f"{ledger['contradicted']} contradicted claim(s), none thesis-critical")
    else:
        drivers.append("no claim contradicted by retrieved evidence")

    if ledger.get("requires_audit"):
        drivers.append(f"{ledger['requires_audit']} company-reported metric(s) awaiting audit")

    weak = [
        d
        for d in scorecard.dimensions
        if d.assessed and (d.score or 0) < 50 and d.information_ratio >= THIN_COVERAGE
    ]
    if weak:
        drivers.append("established weakness in " + ", ".join(d.label.lower() for d in weak[:3]))

    incomplete = [
        d
        for d in scorecard.dimensions
        if d.applicable and d.assessed and d.information_ratio < THIN_COVERAGE
    ]
    if incomplete:
        drivers.append(
            "evidence incomplete for " + ", ".join(d.label.lower() for d in incomplete[:3])
        )

    drivers.append(f"assessment confidence {scorecard.overall_confidence:.2f}")
    return drivers


#: The analyst's own priority label is a strong prior on impact, but not the
#: whole answer -- a "high" question that resolves a contradicted thesis claim
#: outranks a "critical" one attached to nothing in particular.
_PRIORITY_RANK = {
    QuestionPriority.CRITICAL: 4.0,
    QuestionPriority.HIGH: 2.5,
    QuestionPriority.MEDIUM: 1.0,
    QuestionPriority.LOW: 0.5,
}


def rank_questions(
    questions: list[Question],
    claim_summaries: list[dict[str, Any]],
    *,
    limit: int = TOP_QUESTIONS,
) -> list[tuple[Question, str]]:
    """The questions whose answers would most change the decision.

    A list of fifteen good questions is not a diligence plan; it is a way of
    not choosing. Ranking is by expected impact: a question attached to a
    contradicted thesis-critical claim can flip the recommendation, while one
    attached to a corroborated peripheral claim cannot.

    Returns ``(question, why_it_matters)`` pairs, highest impact first.
    """
    by_claim = {c.get("claim_id"): c for c in claim_summaries}
    scored: list[tuple[float, Question, str]] = []

    for question in questions:
        reasons: list[str] = []
        linked = [by_claim[cid] for cid in question.claim_ids if cid in by_claim]

        # Impact is set by the *most consequential* thing a question resolves,
        # not by how many claims it touches. Summing per-claim bonuses ranks a
        # broad question about five settled claims above a narrow one about a
        # single contradiction, which is backwards.
        best_claim_impact = 0.0
        for claim in linked:
            state = claim.get("evidence_state")
            critical = bool(claim.get("is_thesis_critical"))
            value = 0.0
            if state == EvidenceState.CONTRADICTED.value:
                value = 4.0 if critical else 2.0
                reasons.append("resolves a contradicted claim")
            elif state == EvidenceState.COMPANY_REPORTED.value:
                value = 2.4 if critical else 1.0
                reasons.append("audits a company-reported figure")
            elif state == EvidenceState.PLAUSIBLE_UNVERIFIED.value:
                value = 2.0 if critical else 0.8
                reasons.append("closes an unverified thesis claim")
            value += float(claim.get("importance") or 0.0)
            best_claim_impact = max(best_claim_impact, value)

        impact = _PRIORITY_RANK.get(question.priority, 1.0) + best_claim_impact
        # Breadth is worth a little, but never enough to outrank severity.
        impact += min(0.6, 0.15 * max(0, len(linked) - 1))

        if not linked:
            # Unattached questions are generic by construction: nothing in the
            # analysis tells us answering them would change anything.
            impact -= 1.0
            reasons.append("general diligence, not tied to a specific finding")

        why = "; ".join(dict.fromkeys(reasons)) or "supports the overall assessment"
        scored.append((impact, question, why))

    scored.sort(key=lambda triple: triple[0], reverse=True)
    return [(question, why) for _, question, why in scored[:limit]]
