"""Scoring, corroboration, adjudication safeguards, and the rule engine.

These tests encode the correction made after the Moderna review, whose single
root cause was that the scorer asked one question of every claim ("what
experiment does the deck describe?") and scored zero for anything that
described none — so an FDA-approved product scored identically to a slogan.

The invariants asserted here are the product's contract:

* absence of evidence never reduces a score;
* only genuine contradiction does;
* claim type decides the scoring model;
* statements that cannot be true or false today are excluded, not penalised.
"""

from __future__ import annotations

import pytest

from app.analysis.adjudicator import AdjudicatedEvidence, Adjudicator, ClaimAdjudication
from app.analysis.corroboration import CorroborationAssessment, assess_corroboration
from app.analysis.rules import ClaimContext, evaluate
from app.analysis.scoring import ClaimScoringInput, band_for, score_claim, score_run
from app.core.enums import (
    ADVERSE_CORROBORATION,
    UNCHECKED_CORROBORATION,
    ClaimCategory,
    ClaimType,
    CorroborationStatus,
    CredibilityBand,
    EvidenceGrade,
    EvidenceSource,
    EvidenceTier,
    PublicationType,
    QuoteVerification,
    RiskSeverity,
    Stance,
)
from app.evidence.models import EvidenceRecord
from app.evidence.retriever import ScoredRecord
from app.llm.base import LLMRequest, LLMResponse, Usage
from app.llm.client import LLMClient


# ------------------------------------------------------------------ helpers ---
def make_record(**kwargs) -> EvidenceRecord:
    base = {
        "source": EvidenceSource.PUBMED,
        "external_id": "1",
        "pmid": "1",
        "title": "LRRK2 inhibition reduces neurodegeneration",
        "abstract": (
            "We treated mice with an LRRK2 inhibitor. Dopaminergic neuron loss was "
            "significantly reduced compared with vehicle controls over twelve weeks "
            "of continuous dosing in this preclinical model of Parkinson disease."
        ),
        "publication_year": 2023,
        "publication_types": ["Journal Article"],
    }
    return EvidenceRecord(**{**base, **kwargs})


def make_evidence(stance: Stance, strength: float = 0.9, **kwargs) -> AdjudicatedEvidence:
    return AdjudicatedEvidence(
        record=make_record(**kwargs),
        stance=stance,
        relevance=0.9,
        strength=strength,
        similarity=0.7,
        rationale="r",
        supporting_quote="Dopaminergic neuron loss was significantly reduced",
        quote_verification=QuoteVerification.EXACT,
        quote_match_score=1.0,
        weighted_strength=strength,
    )


def make_claim(**kwargs) -> ClaimScoringInput:
    base = {
        "claim_id": "clm_1",
        "claim_type": ClaimType.PRECLINICAL_RESULT,
        "category": ClaimCategory.PRECLINICAL_EFFICACY,
        "claimed_tier": EvidenceTier.IN_VIVO_ANIMAL,
        "importance": 0.8,
        "is_thesis_critical": True,
        "hedging_language": False,
        "quote_verification": QuoteVerification.EXACT,
        "extraction_confidence": 0.9,
        "has_sample_size": True,
        "has_statistics": True,
        "has_comparator": True,
        "has_effect_size": True,
    }
    return ClaimScoringInput(**{**base, **kwargs})


def corroboration(status: CorroborationStatus, **kwargs) -> CorroborationAssessment:
    return CorroborationAssessment(
        claim_id=kwargs.pop("claim_id", "clm_1"),
        status=status,
        confidence=kwargs.pop("confidence", 0.6),
        **kwargs,
    )


# ============================================ the Moderna regression ===
class TestAbsenceIsNotEvidenceAgainst:
    """The failure that motivated the rewrite, asserted directly."""

    def test_unverified_approval_is_not_treated_as_weak(self):
        """An FDA-approved product BioIntel could not verify must not score as unsupported.

        This is the exact Moderna case: mRESVIA is approved, but Drugs@FDA does
        not index CBER-licensed vaccines, so the check returns nothing.
        """
        claim = make_claim(
            claim_type=ClaimType.REGULATORY_APPROVAL,
            category=ClaimCategory.REGULATORY,
            claimed_tier=EvidenceTier.NONE_STATED,
        )
        score = score_claim(claim, corroboration(CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED))
        assert score.credibility_score > 70
        assert score.band in (CredibilityBand.STRONG, CredibilityBand.MODERATE)

    def test_approval_and_slogan_no_longer_score_the_same(self):
        """The specific defect: both scored 27.5/100 under the old model."""
        approval = score_claim(
            make_claim(
                claim_type=ClaimType.REGULATORY_APPROVAL,
                category=ClaimCategory.REGULATORY,
                claimed_tier=EvidenceTier.NONE_STATED,
            ),
            corroboration(CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED),
        )
        slogan = score_claim(
            make_claim(claim_type=ClaimType.MARKETING, category=ClaimCategory.OTHER),
            None,
        )
        assert approval.scored is True
        assert slogan.scored is False
        assert approval.credibility_score > 70

    @pytest.mark.parametrize("status", sorted(UNCHECKED_CORROBORATION))
    def test_unchecked_statuses_leave_the_score_at_its_prior(self, status):
        claim = make_claim()
        unchecked = score_claim(claim, corroboration(status))
        baseline = score_claim(claim, None)
        assert unchecked.credibility_score == pytest.approx(baseline.credibility_score, abs=0.01)
        assert unchecked.breakdown["corroboration_effect"] == 0.0
        assert unchecked.breakdown["absence_penalised"] is False

    def test_only_adverse_statuses_can_reduce_a_score(self):
        claim = make_claim()
        prior_score = score_claim(claim, None).credibility_score
        for status in CorroborationStatus:
            score = score_claim(claim, corroboration(status))
            if not score.scored:
                continue
            if status in ADVERSE_CORROBORATION:
                assert score.credibility_score < prior_score, status
            else:
                assert score.credibility_score >= prior_score - 0.01, status


class TestClaimTypeDrivesScoring:
    def test_regulatory_claims_start_higher_than_mechanistic_ones(self):
        """A public matter of record and an untested hypothesis are not alike."""
        regulatory = score_claim(
            make_claim(
                claim_type=ClaimType.REGULATORY_APPROVAL,
                category=ClaimCategory.REGULATORY,
                claimed_tier=EvidenceTier.NONE_STATED,
            ),
            corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE),
        )
        mechanism = score_claim(
            make_claim(
                claim_type=ClaimType.MECHANISM,
                category=ClaimCategory.MECHANISM,
                claimed_tier=EvidenceTier.NONE_STATED,
            ),
            corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE),
        )
        assert regulatory.credibility_score > mechanism.credibility_score + 25

    def test_mechanism_claims_are_moved_most_by_evidence(self):
        """Leverage differs by type: the literature can settle a mechanism."""
        mechanism = make_claim(claim_type=ClaimType.MECHANISM, category=ClaimCategory.MECHANISM)
        regulatory = make_claim(
            claim_type=ClaimType.REGULATORY_APPROVAL, category=ClaimCategory.REGULATORY
        )
        mech_delta = (
            score_claim(
                mechanism, corroboration(CorroborationStatus.CORROBORATED)
            ).credibility_score
            - score_claim(mechanism, None).credibility_score
        )
        reg_delta = (
            score_claim(
                regulatory, corroboration(CorroborationStatus.CORROBORATED)
            ).credibility_score
            - score_claim(regulatory, None).credibility_score
        )
        assert mech_delta > reg_delta

    @pytest.mark.parametrize(
        "claim_type",
        [
            ClaimType.MARKETING,
            ClaimType.CORPORATE_VISION,
            ClaimType.FORWARD_LOOKING,
            ClaimType.FINANCIAL_GUIDANCE,
            ClaimType.STRATEGIC_OBJECTIVE,
            ClaimType.MARKET_ESTIMATE,
        ],
    )
    def test_unscorable_types_are_excluded_not_penalised(self, claim_type):
        score = score_claim(make_claim(claim_type=claim_type), None)
        assert score.scored is False
        assert score.corroboration is CorroborationStatus.NOT_ASSESSABLE
        assert "Excluded from credibility scoring" in score.explanation

    def test_forward_looking_claims_do_not_drag_the_run_score_down(self):
        """A company is not marked down for having a strategy."""
        strong = make_claim(claim_id="a", claim_type=ClaimType.REGULATORY_APPROVAL)
        plans = [
            make_claim(claim_id=f"p{i}", claim_type=ClaimType.FORWARD_LOOKING) for i in range(8)
        ]
        strong_score = score_claim(strong, corroboration(CorroborationStatus.CORROBORATED))
        plan_scores = [score_claim(p, None) for p in plans]

        alone = score_run([strong_score], {"a": strong})
        with_plans = score_run(
            [strong_score, *plan_scores], {"a": strong, **{p.claim_id: p for p in plans}}
        )
        assert with_plans.score == pytest.approx(alone.score, abs=0.01)
        assert with_plans.breakdown["claims_excluded_by_type"] == 8


class TestClaimScoring:
    def test_corroboration_raises_the_score(self):
        claim = make_claim()
        assert (
            score_claim(claim, corroboration(CorroborationStatus.CORROBORATED)).credibility_score
            > score_claim(claim, None).credibility_score
        )

    def test_contradiction_lowers_the_score(self):
        claim = make_claim()
        assert (
            score_claim(claim, corroboration(CorroborationStatus.CONTRADICTED)).credibility_score
            < score_claim(claim, None).credibility_score
        )

    def test_partial_corroboration_sits_between(self):
        claim = make_claim()
        full = score_claim(claim, corroboration(CorroborationStatus.CORROBORATED))
        partial = score_claim(claim, corroboration(CorroborationStatus.PARTIALLY_CORROBORATED))
        none = score_claim(claim, corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE))
        assert none.credibility_score < partial.credibility_score < full.credibility_score

    def test_stronger_claimed_tier_scores_higher(self):
        assert (
            score_claim(
                make_claim(claimed_tier=EvidenceTier.CLINICAL_PHASE_3), None
            ).credibility_score
            > score_claim(make_claim(claimed_tier=EvidenceTier.IN_VITRO), None).credibility_score
        )

    def test_missing_rigour_lowers_a_quantitative_claim(self):
        rigorous = score_claim(make_claim(), None)
        vague = score_claim(
            make_claim(has_sample_size=False, has_statistics=False, has_comparator=False),
            None,
        )
        assert vague.credibility_score < rigorous.credibility_score

    def test_hedging_is_penalised_mildly(self):
        plain = score_claim(make_claim(), None).credibility_score
        hedged = score_claim(make_claim(hedging_language=True), None).credibility_score
        assert 0 < plain - hedged < 10

    def test_breakdown_is_complete_and_explainable(self):
        breakdown = score_claim(
            make_claim(), corroboration(CorroborationStatus.CORROBORATED)
        ).breakdown
        for key in (
            "claim_type",
            "verifiability",
            "prior",
            "base",
            "corroboration_status",
            "corroboration_effect",
            "evidence_leverage",
            "absence_penalised",
        ):
            assert key in breakdown

    def test_every_score_carries_a_plain_language_explanation(self):
        for status in CorroborationStatus:
            score = score_claim(make_claim(), corroboration(status))
            assert len(score.explanation) > 60, status

    def test_score_is_bounded(self):
        for status in CorroborationStatus:
            for tier in EvidenceTier:
                score = score_claim(make_claim(claimed_tier=tier), corroboration(status))
                assert 0.0 <= score.credibility_score <= 100.0

    @pytest.mark.parametrize(
        ("score", "band"),
        [
            (90.0, CredibilityBand.STRONG),
            (65.0, CredibilityBand.MODERATE),
            (50.0, CredibilityBand.LIMITED),
            (35.0, CredibilityBand.WEAK),
            (10.0, CredibilityBand.UNSUPPORTED),
        ],
    )
    def test_bands(self, score, band):
        assert band_for(score) is band


class TestRunScoring:
    def test_no_scorable_claims_reports_why(self):
        result = score_run([], {})
        assert result.score == 0.0
        assert "forward-looking or promotional" in result.breakdown["reason"]

    def test_contradicted_critical_claim_incurs_a_penalty(self):
        claim = make_claim(claim_id="c1")
        contradicted = score_claim(claim, corroboration(CorroborationStatus.CONTRADICTED))
        result = score_run([contradicted], {"c1": claim})
        assert result.breakdown["penalties"]["contradiction"] > 0

    def test_absence_carries_no_penalty_at_run_level(self):
        claim = make_claim(claim_id="c1")
        unchecked = score_claim(claim, corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE))
        result = score_run([unchecked], {"c1": claim})
        assert result.breakdown["penalties"]["absence_of_evidence"] == 0.0

    def test_unchecked_claims_lower_confidence_not_score(self):
        """The honest place for "we could not check much of this"."""
        claim = make_claim(claim_id="c1")
        checked = score_run(
            [score_claim(claim, corroboration(CorroborationStatus.CORROBORATED))], {"c1": claim}
        )
        unchecked = score_run(
            [score_claim(claim, corroboration(CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED))],
            {"c1": claim},
        )
        assert unchecked.confidence < checked.confidence

    def test_deterministic(self):
        claim = make_claim(claim_id="c1")
        score = score_claim(claim, corroboration(CorroborationStatus.CORROBORATED))
        assert score_run([score], {"c1": claim}).score == score_run([score], {"c1": claim}).score


# ================================================== corroboration ===
class TestCorroborationAssessment:
    def test_nothing_retrieved_yields_the_type_specific_null_status(self):
        regulatory = assess_corroboration(
            claim_id="c1", claim_type=ClaimType.REGULATORY_APPROVAL, adjudication=None
        )
        mechanism = assess_corroboration(
            claim_id="c2", claim_type=ClaimType.MECHANISM, adjudication=None
        )
        assert regulatory.status is CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
        assert mechanism.status is CorroborationStatus.INSUFFICIENT_EVIDENCE

    def test_null_result_says_it_is_about_the_search(self):
        assessment = assess_corroboration(
            claim_id="c1", claim_type=ClaimType.MECHANISM, adjudication=None
        )
        assert "not evidence that the claim is false" in assessment.rationale

    def test_unscorable_types_are_not_assessable(self):
        assessment = assess_corroboration(
            claim_id="c1", claim_type=ClaimType.MARKETING, adjudication=None
        )
        assert assessment.status is CorroborationStatus.NOT_ASSESSABLE

    def test_strong_support_from_a_capable_grade_corroborates(self):
        pivotal = make_record(
            journal="The New England Journal of Medicine",
            title="A Phase 3 randomized trial",
            publication_types=["Randomized Controlled Trial", "Clinical Trial, Phase III"],
        )
        evidence = make_evidence(Stance.SUPPORTS)
        evidence.record = pivotal
        assessment = assess_corroboration(
            claim_id="c1",
            claim_type=ClaimType.CLINICAL_RESULT,
            adjudication=ClaimAdjudication(claim_id="c1", evidence=[evidence] * 3),
        )
        assert assessment.status is CorroborationStatus.CORROBORATED

    def test_weak_grade_support_is_plausible_not_corroborated(self):
        """A narrative review cannot corroborate a clinical result."""
        review = make_record(title="A review of the field", publication_types=["Review"])
        evidence = make_evidence(Stance.SUPPORTS)
        evidence.record = review
        assessment = assess_corroboration(
            claim_id="c1",
            claim_type=ClaimType.CLINICAL_RESULT,
            adjudication=ClaimAdjudication(claim_id="c1", evidence=[evidence] * 3),
        )
        assert assessment.status is CorroborationStatus.PLAUSIBLE_UNVERIFIED

    def test_genuine_disagreement_contradicts(self):
        assessment = assess_corroboration(
            claim_id="c1",
            claim_type=ClaimType.MECHANISM,
            adjudication=ClaimAdjudication(
                claim_id="c1", evidence=[make_evidence(Stance.CONTRADICTS)] * 3
            ),
        )
        assert assessment.status is CorroborationStatus.CONTRADICTED
        assert assessment.is_adverse

    def test_balanced_evidence_is_disputed_not_contradicted(self):
        assessment = assess_corroboration(
            claim_id="c1",
            claim_type=ClaimType.MECHANISM,
            adjudication=ClaimAdjudication(
                claim_id="c1",
                evidence=[make_evidence(Stance.SUPPORTS)] * 3
                + [make_evidence(Stance.CONTRADICTS)] * 3,
            ),
        )
        assert assessment.status is CorroborationStatus.DISPUTED

    def test_off_topic_evidence_is_ignored(self):
        weak = make_evidence(Stance.SUPPORTS)
        weak.relevance = 0.05
        assessment = assess_corroboration(
            claim_id="c1",
            claim_type=ClaimType.MECHANISM,
            adjudication=ClaimAdjudication(claim_id="c1", evidence=[weak]),
        )
        assert assessment.status is CorroborationStatus.INSUFFICIENT_EVIDENCE

    def test_authoritative_verification_outranks_literature(self):
        from app.analysis.verification import VerificationResult
        from app.core.enums import VerificationStatus

        assessment = assess_corroboration(
            claim_id="c1",
            claim_type=ClaimType.PIPELINE_STAGE,
            adjudication=ClaimAdjudication(
                claim_id="c1", evidence=[make_evidence(Stance.SUPPORTS)] * 5
            ),
            verification=VerificationResult(
                claim_id="c1",
                status=VerificationStatus.REFUTED,
                source="clinicaltrials_gov",
                detail="The registry shows Phase 1, not the asserted Phase 3.",
                confidence=0.8,
            ),
        )
        assert assessment.status is CorroborationStatus.CONTRADICTED
        assert assessment.authoritative is True

    def test_every_assessment_carries_a_rationale(self):
        for claim_type in ClaimType:
            assessment = assess_corroboration(
                claim_id="c1", claim_type=claim_type, adjudication=None
            )
            assert assessment.rationale, claim_type


# ================================================= adjudication ===
class TestAdjudicationSafeguards:
    def _adjudicator_returning(self, payload: dict) -> Adjudicator:
        import json

        class Provider:
            name = "canned"

            async def complete_structured(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    text=json.dumps(payload),
                    model="m",
                    usage=Usage(),
                    latency_ms=1,
                    provider="canned",
                )

            async def embed(self, texts, *, model, dimensions):
                return []

            async def aclose(self):
                return None

        return Adjudicator(LLMClient(Provider(), persist_logs=False))

    def _adjudication(self, **overrides) -> dict:
        base = {
            "evidence_ref": "E1",
            "stance": "supports",
            "relevance": 0.9,
            "strength": 0.9,
            "supporting_quote": "Dopaminergic neuron loss was significantly reduced",
            "rationale": "r",
            "caveats": [],
            "study_design": "preclinical",
            "addresses_claim_directly": True,
        }
        return {"adjudications": [{**base, **overrides}]}

    async def _run(self, adjudicator: Adjudicator, record: EvidenceRecord | None = None):
        return await adjudicator.adjudicate_claim(
            claim_id="c1",
            claim_statement="s",
            claim_quote="q",
            claim_category="mechanism",
            claim_type="mechanism",
            corroboration_guidance="g",
            claimed_tier="in_vivo_animal",
            records=[
                ScoredRecord(
                    record=record or make_record(), relevance=0.9, quality=0.8, rank_score=0.9
                )
            ],
        )

    async def test_unverifiable_quote_downgrades_stance_to_neutral(self):
        """A model that cannot quote the abstract must not move the score."""
        adjudicator = self._adjudicator_returning(
            self._adjudication(supporting_quote="The drug cured every patient in the cohort.")
        )
        result = await self._run(adjudicator)
        assert result.evidence[0].stance is Stance.NEUTRAL
        assert any("could not be verified" in c for c in result.evidence[0].caveats)

    async def test_verifiable_quote_keeps_the_stance(self):
        adjudicator = self._adjudicator_returning(self._adjudication())
        result = await self._run(adjudicator)
        assert result.evidence[0].stance is Stance.SUPPORTS
        assert result.evidence[0].weighted_strength > 0

    async def test_retracted_record_is_neutralised(self):
        adjudicator = self._adjudicator_returning(self._adjudication())
        record = make_record(publication_types=["Retracted Publication"], is_retracted=True)
        result = await self._run(adjudicator, record)
        assert result.evidence[0].stance is Stance.NEUTRAL
        assert any("retracted" in c.lower() for c in result.evidence[0].caveats)

    async def test_unknown_evidence_reference_is_dropped(self):
        adjudicator = self._adjudicator_returning(self._adjudication(evidence_ref="E99"))
        result = await self._run(adjudicator)
        assert result.evidence == []

    async def test_no_records_returns_empty(self):
        adjudicator = self._adjudicator_returning({"adjudications": []})
        result = await adjudicator.adjudicate_claim(
            claim_id="c1",
            claim_statement="s",
            claim_quote="q",
            claim_category="mechanism",
            claimed_tier="in_vitro",
            records=[],
        )
        assert result.evidence == []


# ======================================================== rules ===
class TestRules:
    def _context(self, claim: ClaimScoringInput, corr=None, **kwargs) -> ClaimContext:
        return ClaimContext(
            claim_id=claim.claim_id,
            statement=kwargs.pop("statement", "NG-101 slows neurodegeneration in patients"),
            page_number=kwargs.pop("page_number", 4),
            scoring=claim,
            score=score_claim(claim, corr),
            adjudication=kwargs.pop("adjudication", None),
            corroboration=corr,
            **kwargs,
        )

    def _rule_ids(self, contexts) -> set[str]:
        return {f.rule_id for f in evaluate(contexts)}

    def test_contradicted_thesis_claim_is_critical(self):
        findings = evaluate(
            [self._context(make_claim(), corroboration(CorroborationStatus.CONTRADICTED))]
        )
        contradicted = [f for f in findings if f.rule_id == "contradicted_thesis_claim"]
        assert contradicted and contradicted[0].severity is RiskSeverity.CRITICAL

    def test_uncorroborated_claim_is_flagged_as_a_search_result(self):
        ids = self._rule_ids(
            [self._context(make_claim(), corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE))]
        )
        assert "uncorroborated_thesis_claim" in ids
        assert "contradicted_thesis_claim" not in ids

    def test_unverifiable_claim_gets_its_own_finding(self):
        """Distinct from "no evidence found": the remedy is different."""
        ids = self._rule_ids(
            [
                self._context(
                    make_claim(claim_type=ClaimType.REGULATORY_APPROVAL),
                    corroboration(CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED),
                )
            ]
        )
        assert "unverifiable_thesis_claim" in ids
        assert "uncorroborated_thesis_claim" not in ids

    def test_unscorable_claims_generate_no_evidence_findings(self):
        ids = self._rule_ids([self._context(make_claim(claim_type=ClaimType.MARKETING))])
        assert "uncorroborated_thesis_claim" not in ids
        assert "contradicted_thesis_claim" not in ids

    def test_approval_claim_is_not_flagged_for_stating_no_evidence_tier(self):
        """An approval describes no experiment; that is correct, not a defect."""
        ids = self._rule_ids(
            [
                self._context(
                    make_claim(
                        claim_type=ClaimType.REGULATORY_APPROVAL,
                        claimed_tier=EvidenceTier.NONE_STATED,
                    ),
                    corroboration(CorroborationStatus.CORROBORATED),
                )
            ]
        )
        assert "assertion_without_data" not in ids

    def test_result_claim_without_a_tier_is_flagged(self):
        ids = self._rule_ids(
            [
                self._context(
                    make_claim(
                        claim_type=ClaimType.CLINICAL_RESULT,
                        claimed_tier=EvidenceTier.NONE_STATED,
                        importance=0.9,
                    ),
                    corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE),
                )
            ]
        )
        assert "assertion_without_data" in ids

    def test_clinical_claim_on_preclinical_evidence_flags_translational_gap(self):
        claim = make_claim(
            category=ClaimCategory.CLINICAL_EFFICACY, claimed_tier=EvidenceTier.IN_VIVO_ANIMAL
        )
        assert "translational_gap" in self._rule_ids(
            [self._context(claim, corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE))]
        )

    def test_missing_statistics_control_and_n_each_flag(self):
        claim = make_claim(
            has_statistics=False, has_comparator=False, has_sample_size=False, has_effect_size=True
        )
        ids = self._rule_ids(
            [self._context(claim, corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE))]
        )
        assert {"effect_without_statistics", "effect_without_control", "effect_without_n"} <= ids

    def test_fuzzy_quote_is_flagged(self):
        claim = make_claim(quote_verification=QuoteVerification.FUZZY)
        assert "approximate_provenance" in self._rule_ids(
            [
                self._context(
                    claim,
                    corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE),
                    quote_match_score=0.9,
                )
            ]
        )

    def test_terminated_trial_precedent_is_surfaced(self):
        trial = make_record(
            source=EvidenceSource.CLINICALTRIALS_GOV, external_id="NCT1", pmid=None, nct_id="NCT1"
        )
        trial.trial = {"status": "TERMINATED", "why_stopped": "Futility"}
        evidence = make_evidence(Stance.NEUTRAL)
        evidence.record = trial
        findings = evaluate(
            [
                self._context(
                    make_claim(),
                    corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE),
                    adjudication=ClaimAdjudication(claim_id="clm_1", evidence=[evidence]),
                )
            ]
        )
        stopped = [f for f in findings if f.rule_id == "terminated_trial_precedent"]
        assert stopped and "Futility" in stopped[0].description

    def test_retracted_evidence_is_surfaced(self):
        retracted = make_record(publication_types=["Retracted Publication"], is_retracted=True)
        evidence = make_evidence(Stance.NEUTRAL)
        evidence.record = retracted
        assert "retracted_evidence" in self._rule_ids(
            [
                self._context(
                    make_claim(),
                    corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE),
                    adjudication=ClaimAdjudication(claim_id="clm_1", evidence=[evidence]),
                )
            ]
        )

    def test_preprint_only_support_is_flagged(self):
        preprint = make_record(is_preprint=True)
        evidence = make_evidence(Stance.SUPPORTS)
        evidence.record = preprint
        assert "support_only_from_preprints" in self._rule_ids(
            [
                self._context(
                    make_claim(),
                    corroboration(CorroborationStatus.PARTIALLY_CORROBORATED),
                    adjudication=ClaimAdjudication(claim_id="clm_1", evidence=[evidence]),
                )
            ]
        )

    def test_low_coverage_is_a_portfolio_finding(self):
        contexts = [
            self._context(
                make_claim(claim_id=f"clm_{i}", is_thesis_critical=False),
                corroboration(CorroborationStatus.INSUFFICIENT_EVIDENCE),
            )
            for i in range(6)
        ]
        assert "low_literature_coverage" in self._rule_ids(contexts)

    def test_findings_are_sorted_by_severity(self):
        claim = make_claim(quote_verification=QuoteVerification.FUZZY, has_statistics=False)
        findings = evaluate([self._context(claim, corroboration(CorroborationStatus.CONTRADICTED))])
        order = {
            RiskSeverity.CRITICAL: 0,
            RiskSeverity.HIGH: 1,
            RiskSeverity.MEDIUM: 2,
            RiskSeverity.LOW: 3,
            RiskSeverity.INFO: 4,
        }
        ranks = [order[f.severity] for f in findings]
        assert ranks == sorted(ranks)

    def test_empty_input_produces_nothing(self):
        assert evaluate([]) == []


# ============================================== evidence grading ===
class TestEvidenceHierarchy:
    def test_hierarchy_is_ordered_as_documented(self):
        from app.evidence.grading import grade_record

        nejm = grade_record(
            make_record(
                journal="The New England Journal of Medicine",
                title="A Phase 3 randomized trial",
                publication_types=["Randomized Controlled Trial", "Clinical Trial, Phase III"],
            )
        )
        elsewhere = grade_record(
            make_record(
                journal="Vaccine",
                title="A Phase 3 randomized trial",
                publication_types=["Randomized Controlled Trial", "Clinical Trial, Phase III"],
            )
        )
        phase2 = grade_record(
            make_record(title="A Phase 2 study", publication_types=["Clinical Trial, Phase II"])
        )
        review = grade_record(make_record(title="A review", publication_types=["Review"]))
        retracted = grade_record(
            make_record(publication_types=["Retracted Publication"], is_retracted=True)
        )

        assert nejm.weight > elsewhere.weight > phase2.weight > review.weight > retracted.weight
        assert nejm.grade is EvidenceGrade.PIVOTAL_TRIAL_TOP_JOURNAL
        assert retracted.weight == 0.0

    def test_registry_records_are_authoritative_for_existence(self):
        from app.evidence.grading import grade_record

        graded = grade_record(
            make_record(
                source=EvidenceSource.CLINICALTRIALS_GOV,
                external_id="NCT1",
                trial={"phases": ["PHASE3"], "has_results": True, "status": "COMPLETED"},
            )
        )
        assert graded.grade is EvidenceGrade.REGISTRY_WITH_RESULTS
        assert graded.is_authoritative is True

    def test_every_grade_carries_a_rationale(self):
        from app.evidence.grading import grade_record

        for types in (["Review"], ["Meta-Analysis"], ["Case Reports"], []):
            assert grade_record(make_record(publication_types=types)).rationale

    def test_study_design_inference_still_works(self):
        assert (
            make_record(
                title="Effect in mice", abstract="Mice were treated in vitro and in vivo."
            ).study_design
            is PublicationType.PRECLINICAL
        )
