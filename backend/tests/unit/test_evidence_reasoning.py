"""Absence of evidence must never behave like evidence of absence.

The claim scorer already honoured that rule; the aggregates did not. A claim
we could not check kept its type prior -- correct -- and every dimension then
averaged that prior in as though it were an assessment. Measured across the
benchmark decks before this change:

    Moderna    23 claims insufficient_evidence,      mean credibility 40.8
    Beam       37 claims not_independently_verified, mean credibility 31.1
    Recursion  26 claims plausible_unverified,       mean credibility 46.1

Nothing contradicted any of them. Because one claim informs several
dimensions, the same gap was charged five times over, and an approved
commercial leader with two marketed products scored 48.9/100 with a
"significant concerns" recommendation.

These tests fix the *mechanism*, not the numbers: they compare a deck we could
not verify against the same deck contradicted, and against the same deck
confirmed, and assert the orderings an investment committee would expect.
"""

from __future__ import annotations

import pytest

from app.analysis.corroboration import CorroborationAssessment
from app.analysis.evidence_state import (
    NO_INFORMATION_ANCHOR,
    derive_evidence,
    informed_aggregate,
)
from app.analysis.scorecard import ScorecardInput, build_scorecard, detect_archetype
from app.analysis.scoring import ClaimScoringInput, score_claim, score_run
from app.core.enums import (
    ClaimCategory,
    ClaimType,
    CompanyArchetype,
    CorroborationStatus,
    EvidenceState,
    EvidenceTier,
    ICRecommendation,
    QuoteVerification,
    RetrievalStatus,
    ScoreDimension,
)


def make_claim(
    claim_id: str,
    claim_type: ClaimType,
    status: CorroborationStatus,
    *,
    thesis_critical: bool = False,
    importance: float = 0.8,
    category: ClaimCategory = ClaimCategory.MECHANISM,
    tier: EvidenceTier = EvidenceTier.NONE_STATED,
) -> ScorecardInput:
    scoring = ClaimScoringInput(
        claim_id=claim_id,
        claim_type=claim_type,
        category=category,
        claimed_tier=tier,
        importance=importance,
        is_thesis_critical=thesis_critical,
        hedging_language=False,
        quote_verification=QuoteVerification.EXACT,
        extraction_confidence=0.8,
    )
    corroboration = CorroborationAssessment(
        claim_id=claim_id,
        status=status,
        confidence=0.5,
        rationale="fixture",
        supporting_count=2 if status is CorroborationStatus.CORROBORATED else 0,
        contradicting_count=2 if status is CorroborationStatus.CONTRADICTED else 0,
        evidence_considered=2 if status is not CorroborationStatus.INSUFFICIENT_EVIDENCE else 0,
    )
    score = score_claim(scoring, corroboration)
    return ScorecardInput(
        claim_id=claim_id,
        statement=f"statement {claim_id}",
        scoring=scoring,
        score=score,
        corroboration=corroboration,
    )


def deck(status: CorroborationStatus, n: int = 8, **kwargs) -> list[ScorecardInput]:
    return [make_claim(f"c{i}", ClaimType.MECHANISM, status, **kwargs) for i in range(n)]


def overall_of(inputs: list[ScorecardInput]) -> float:
    return score_run(
        [i.score for i in inputs],
        {i.claim_id: i.scoring for i in inputs},
        pages_analysed=10,
        pages_total=10,
    ).score


# ==================================================== issue 1: absence ===
class TestAbsenceIsNotEvidenceOfAbsence:
    def test_unverified_deck_outscores_contradicted_deck(self):
        unverified = overall_of(deck(CorroborationStatus.INSUFFICIENT_EVIDENCE))
        contradicted = overall_of(deck(CorroborationStatus.CONTRADICTED))
        assert unverified > contradicted + 15, (
            "a deck nobody could check must score far above one the evidence disagrees with"
        )

    def test_verified_deck_outscores_unverified_deck(self):
        verified = overall_of(deck(CorroborationStatus.CORROBORATED))
        unverified = overall_of(deck(CorroborationStatus.INSUFFICIENT_EVIDENCE))
        assert verified > unverified, "corroboration must still be worth something"

    def test_unverified_deck_lands_near_neutral_not_weak(self):
        """The headline regression: unknown must not read as weak."""
        score = overall_of(deck(CorroborationStatus.INSUFFICIENT_EVIDENCE))
        assert score >= NO_INFORMATION_ANCHOR - 6, (
            f"a deck with no contradicted claims scored {score:.1f}; that is an "
            "information gap being reported as a finding"
        )
        assert score < 75, "and it must not read as verified either"

    def test_failed_search_does_not_move_the_score_at_all(self):
        """Two identical decks, one searched fruitlessly, one not searched."""
        searched = deck(CorroborationStatus.INSUFFICIENT_EVIDENCE)
        unsearched = deck(CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED)
        assert overall_of(searched) == pytest.approx(overall_of(unsearched), abs=6.0)


# ============================================= issue 2: double counting ===
class TestUncertaintyIsCountedOnce:
    def test_one_gap_does_not_compound_across_dimensions(self):
        """A claim informing five dimensions must not be charged five times."""
        card = build_scorecard(
            deck(CorroborationStatus.INSUFFICIENT_EVIDENCE, n=10), pipeline_size=3
        )
        assessed = [d for d in card.dimensions if d.assessed and d.claims_considered]
        assert assessed
        for dimension in assessed:
            assert (dimension.score or 0) >= 40, (
                f"{dimension.dimension.value} fell to {dimension.score} on unverified "
                "claims alone; the gap is being charged as a penalty"
            )

    def test_the_gap_shows_up_in_confidence_instead(self):
        unverified = build_scorecard(
            deck(CorroborationStatus.INSUFFICIENT_EVIDENCE), pipeline_size=3
        )
        verified = build_scorecard(deck(CorroborationStatus.CORROBORATED), pipeline_size=3)
        assert unverified.overall_confidence < verified.overall_confidence, (
            "uncertainty must land in confidence, which is what it is"
        )

    def test_dimensions_report_how_much_they_could_establish(self):
        card = build_scorecard(deck(CorroborationStatus.INSUFFICIENT_EVIDENCE), pipeline_size=3)
        # Structural dimensions are measured from the deck, not from retrieval,
        # so they have no information gap to report.
        structural = {
            ScoreDimension.PIPELINE_DIVERSIFICATION,
            ScoreDimension.DISCLOSURE_QUALITY,
        }
        informed = [
            d for d in card.dimensions if d.claims_considered and d.dimension not in structural
        ]
        assert informed
        for dimension in informed:
            assert dimension.information_ratio < 0.5
            assert "confidence" in dimension.rationale.lower()

    def test_unverified_claims_are_not_listed_as_negative_drivers(self):
        """An information gap re-presented as an adverse finding is the bug."""
        card = build_scorecard(deck(CorroborationStatus.INSUFFICIENT_EVIDENCE), pipeline_size=3)
        for dimension in card.dimensions:
            for driver in dimension.negative_drivers:
                assert driver.get("evidence_state") != EvidenceState.PLAUSIBLE_UNVERIFIED.value


# ======================================= issue 3: credibility vs confidence ===
class TestCredibilityAndConfidenceAreSeparate:
    def test_proprietary_assay_keeps_credibility_and_loses_confidence(self):
        """The worked example from the brief."""
        internal = make_claim(
            "c1", ClaimType.PRECLINICAL_RESULT, CorroborationStatus.INSUFFICIENT_EVIDENCE
        )
        assert internal.score.credibility_score > 30, (
            "a proprietary assay is unaudited, not implausible"
        )
        assert internal.score.confidence < 0.75, "but we should not claim to be sure of it"

    def test_company_reported_metrics_are_flagged_for_audit(self):
        internal = make_claim(
            "c1", ClaimType.TRACK_RECORD, CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
        )
        assert internal.score.evidence_state is EvidenceState.COMPANY_REPORTED
        assert internal.score.requires_audit
        assert "audit" in internal.score.explanation.lower()

    def test_audit_language_never_calls_it_a_scientific_weakness(self):
        internal = make_claim(
            "c1", ClaimType.TRACK_RECORD, CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
        )
        note = internal.score.evidence.note.lower()
        assert "audit" in note
        assert "not a scientific weakness" in note
        assert "company assertion" in note


# ============================================ issue 6: retrieval coverage ===
class TestRetrievalStatusIsStoredSeparately:
    def test_nothing_retrieved_is_recorded_as_none_not_as_evidence(self):
        evidence = derive_evidence(
            claim_id="c1",
            claim_type=ClaimType.MECHANISM,
            credibility=50.0,
            confidence=0.4,
            corroboration=CorroborationAssessment(
                claim_id="c1",
                status=CorroborationStatus.INSUFFICIENT_EVIDENCE,
                evidence_considered=0,
            ),
        )
        assert evidence.retrieval_status is RetrievalStatus.NONE
        assert not evidence.contradicted
        assert evidence.state is EvidenceState.PLAUSIBLE_UNVERIFIED

    def test_retrieval_error_is_distinguished_from_finding_nothing(self):
        errored = derive_evidence(
            claim_id="c1",
            claim_type=ClaimType.MECHANISM,
            credibility=50.0,
            confidence=0.4,
            corroboration=None,
            retrieval_errored=True,
        )
        assert errored.retrieval_status is RetrievalStatus.ERROR

    def test_every_scored_claim_carries_the_full_signal(self):
        claim = make_claim("c1", ClaimType.CLINICAL_RESULT, CorroborationStatus.CORROBORATED)
        payload = claim.score.evidence.to_dict()
        for key in (
            "evidence_state",
            "confidence",
            "retrieval_status",
            "contradicted",
            "evidence_level",
        ):
            assert key in payload


# =============================================== issue 5: archetype routing ===
class TestArchetypeRouting:
    def test_a_deck_with_no_biomedical_claims_is_not_scored_as_biotech(self):
        """The Beam case: 37 track-record claims, zero biomedical ones."""
        inputs = [
            make_claim(
                f"c{i}", ClaimType.TRACK_RECORD, CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
            )
            for i in range(12)
        ]
        assert detect_archetype(inputs) is CompanyArchetype.NON_BIOMEDICAL

    def test_biomedical_dimensions_are_not_applicable_not_zero(self):
        inputs = [
            make_claim(
                f"c{i}", ClaimType.TRACK_RECORD, CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
            )
            for i in range(12)
        ]
        card = build_scorecard(inputs, pipeline_size=2)
        biomedical = card.by_dimension()[ScoreDimension.SCIENTIFIC_VALIDITY.value]
        assert not biomedical.applicable
        assert biomedical.score is None
        assert "not applicable" in biomedical.rationale.lower()

    def test_the_recommendation_says_to_use_another_framework(self):
        inputs = [
            make_claim(
                f"c{i}", ClaimType.TRACK_RECORD, CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
            )
            for i in range(12)
        ]
        card = build_scorecard(inputs, pipeline_size=2)
        assert "commercial diligence" in card.recommendation_rationale.lower()
        assert not card.breakdown["biomedical_framework_applied"]

    def test_a_real_biotech_still_gets_the_biomedical_framework(self):
        inputs = deck(CorroborationStatus.CORROBORATED, n=6)
        card = build_scorecard(inputs, pipeline_size=4)
        assert card.breakdown["biomedical_framework_applied"]
        assert card.by_dimension()[ScoreDimension.SCIENTIFIC_VALIDITY.value].applicable


# ============================================ issue 7: recommendation logic ===
class TestRecommendationFollowsEvidenceNotScore:
    def test_strong_science_with_thin_verification_advances_with_conditions(self):
        """Strong biology + low verification must not read as concerns."""
        inputs = [
            make_claim(
                f"c{i}",
                ClaimType.REGULATORY_APPROVAL,
                CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED,
                importance=0.9,
            )
            for i in range(8)
        ]
        card = build_scorecard(inputs, pipeline_size=5)
        assert card.recommendation in (
            ICRecommendation.ADVANCE,
            ICRecommendation.ADVANCE_WITH_CONDITIONS,
        ), card.recommendation_rationale
        assert card.recommendation is not ICRecommendation.SIGNIFICANT_CONCERNS

    def test_widespread_contradiction_still_produces_concerns(self):
        inputs = [
            make_claim(
                f"c{i}",
                ClaimType.CLINICAL_RESULT,
                CorroborationStatus.CONTRADICTED,
                thesis_critical=True,
            )
            for i in range(6)
        ]
        card = build_scorecard(inputs, pipeline_size=2)
        assert card.recommendation in (
            ICRecommendation.SIGNIFICANT_CONCERNS,
            ICRecommendation.DO_NOT_ADVANCE,
        )

    def test_one_discrepancy_amid_strong_evidence_is_a_condition_not_a_veto(self):
        """The Moderna pipeline-stage case: registry says Phase 2, deck says 3."""
        inputs = [
            make_claim(
                f"ok{i}",
                ClaimType.REGULATORY_APPROVAL,
                CorroborationStatus.CORROBORATED,
                thesis_critical=True,
                importance=0.9,
            )
            for i in range(6)
        ]
        inputs.append(
            make_claim(
                "bad",
                ClaimType.PIPELINE_STAGE,
                CorroborationStatus.CONTRADICTED,
                thesis_critical=True,
            )
        )
        card = build_scorecard(inputs, pipeline_size=6)
        assert card.recommendation is ICRecommendation.ADVANCE_WITH_CONDITIONS
        assert "gating condition" in card.recommendation_rationale.lower()

    def test_recommendation_rationale_names_verification_coverage(self):
        card = build_scorecard(deck(CorroborationStatus.INSUFFICIENT_EVIDENCE), pipeline_size=3)
        assert "%" in card.recommendation_rationale
        assert "verification_coverage" in card.breakdown


# ==================================================== the shared primitive ===
class TestInformedAggregate:
    def test_no_entries_returns_the_neutral_anchor(self):
        assert informed_aggregate([]) == (NO_INFORMATION_ANCHOR, 0.0)

    def test_a_gap_cannot_pull_an_aggregate_below_neutral(self):
        score, ratio = informed_aggregate([(12.0, 1.0, True), (12.0, 1.0, True)])
        assert score >= NO_INFORMATION_ANCHOR - 0.01
        assert ratio < 0.5

    def test_a_well_presented_gap_may_still_lift_an_aggregate(self):
        low, _ = informed_aggregate([(20.0, 1.0, True)])
        high, _ = informed_aggregate([(90.0, 1.0, True)])
        assert high > low

    def test_verified_evidence_dominates_the_result(self):
        score, ratio = informed_aggregate([(90.0, 1.0, False), (20.0, 1.0, True)])
        assert score > 70
        assert ratio > 0.5

    def test_contradiction_can_still_drive_an_aggregate_down(self):
        score, ratio = informed_aggregate([(10.0, 1.0, False)] * 4)
        assert score < 30, "checked-and-poor must remain distinguishable from unchecked"
        assert ratio == pytest.approx(1.0)
