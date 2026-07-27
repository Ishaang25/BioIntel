"""Scoring, adjudication safeguards, and the deterministic rule engine."""

from __future__ import annotations

import pytest

from app.analysis.adjudicator import AdjudicatedEvidence, Adjudicator, ClaimAdjudication
from app.analysis.rules import ClaimContext, evaluate
from app.analysis.scoring import (
    ClaimScoringInput,
    band_for,
    score_claim,
    score_run,
)
from app.core.enums import (
    ClaimCategory,
    CredibilityBand,
    EvidenceSource,
    EvidenceTier,
    QuoteVerification,
    RiskSeverity,
    Stance,
)
from app.evidence.models import EvidenceRecord
from app.evidence.retriever import ScoredRecord
from app.llm.base import LLMRequest, LLMResponse, Usage
from app.llm.client import LLMClient


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


def make_evidence(stance: Stance, strength: float = 0.8, **kwargs) -> AdjudicatedEvidence:
    return AdjudicatedEvidence(
        record=make_record(**kwargs),
        stance=stance,
        relevance=0.85,
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


class TestClaimScoring:
    def test_supporting_evidence_raises_the_score(self):
        claim = make_claim()
        without = score_claim(claim, None)
        with_support = score_claim(
            claim,
            ClaimAdjudication(claim_id="clm_1", evidence=[make_evidence(Stance.SUPPORTS)]),
        )
        assert with_support.credibility_score > without.credibility_score

    def test_contradicting_evidence_lowers_the_score(self):
        claim = make_claim()
        contradicted = score_claim(
            claim,
            ClaimAdjudication(claim_id="clm_1", evidence=[make_evidence(Stance.CONTRADICTS)]),
        )
        assert contradicted.credibility_score < score_claim(claim, None).credibility_score

    def test_contradiction_outweighs_equal_support(self):
        """Disconfirmation is more informative than confirmation."""
        claim = make_claim()
        both = score_claim(
            claim,
            ClaimAdjudication(
                claim_id="clm_1",
                evidence=[make_evidence(Stance.SUPPORTS), make_evidence(Stance.CONTRADICTS)],
            ),
        )
        assert both.credibility_score < score_claim(claim, None).credibility_score

    def test_absence_of_evidence_is_neutral_not_punitive(self):
        """No evidence must not read as evidence against."""
        score = score_claim(make_claim(), None)
        assert score.breakdown["external_component"] == pytest.approx(0.5)
        assert score.breakdown["evidence_confidence"] == pytest.approx(0.0)

    def test_stronger_claimed_tier_scores_higher(self):
        phase_two = score_claim(make_claim(claimed_tier=EvidenceTier.CLINICAL_PHASE_2), None)
        in_vitro = score_claim(make_claim(claimed_tier=EvidenceTier.IN_VITRO), None)
        assert phase_two.credibility_score > in_vitro.credibility_score

    def test_unstated_tier_scores_lowest(self):
        assert (
            score_claim(make_claim(claimed_tier=EvidenceTier.NONE_STATED), None).credibility_score
            < score_claim(make_claim(claimed_tier=EvidenceTier.IN_SILICO), None).credibility_score
        )

    def test_missing_rigor_lowers_score(self):
        rigorous = score_claim(make_claim(), None)
        vague = score_claim(
            make_claim(
                has_sample_size=False,
                has_statistics=False,
                has_comparator=False,
                has_effect_size=False,
            ),
            None,
        )
        assert vague.credibility_score < rigorous.credibility_score

    def test_hedging_is_penalised(self):
        assert (
            score_claim(make_claim(hedging_language=True), None).credibility_score
            < score_claim(make_claim(), None).credibility_score
        )

    def test_breakdown_is_complete_and_explainable(self):
        breakdown = score_claim(make_claim(), None).breakdown
        for key in (
            "internal_component",
            "external_component",
            "tier_weight",
            "rigor",
            "support_mass",
            "contradiction_mass",
            "evidence_confidence",
            "weights",
        ):
            assert key in breakdown

    def test_score_is_bounded(self):
        strong = score_claim(
            make_claim(claimed_tier=EvidenceTier.APPROVED),
            ClaimAdjudication(
                claim_id="clm_1",
                evidence=[make_evidence(Stance.SUPPORTS, 1.0) for _ in range(20)],
            ),
        )
        assert 0.0 <= strong.credibility_score <= 100.0

    def test_consistency_reflects_agreement(self):
        agreeing = score_claim(
            make_claim(),
            ClaimAdjudication(
                claim_id="clm_1", evidence=[make_evidence(Stance.SUPPORTS) for _ in range(4)]
            ),
        )
        split = score_claim(
            make_claim(),
            ClaimAdjudication(
                claim_id="clm_1",
                evidence=[
                    make_evidence(Stance.SUPPORTS),
                    make_evidence(Stance.SUPPORTS),
                    make_evidence(Stance.CONTRADICTS),
                    make_evidence(Stance.CONTRADICTS),
                ],
            ),
        )
        assert agreeing.consistency > split.consistency

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
    def test_no_claims_scores_zero(self):
        result = score_run([], {})
        assert result.score == 0.0
        assert result.band is CredibilityBand.UNSUPPORTED

    def test_thesis_critical_claims_dominate(self):
        critical = make_claim(claim_id="critical", is_thesis_critical=True, importance=0.95)
        trivial = make_claim(
            claim_id="trivial",
            is_thesis_critical=False,
            importance=0.1,
            category=ClaimCategory.MARKET,
        )
        weak_critical = score_claim(
            critical,
            ClaimAdjudication(claim_id="critical", evidence=[make_evidence(Stance.CONTRADICTS)]),
        )
        strong_trivial = score_claim(
            trivial,
            ClaimAdjudication(claim_id="trivial", evidence=[make_evidence(Stance.SUPPORTS)]),
        )
        result = score_run(
            [weak_critical, strong_trivial],
            {"critical": critical, "trivial": trivial},
        )
        assert result.score < strong_trivial.credibility_score

    def test_contradicted_critical_claim_incurs_a_penalty(self):
        claim = make_claim(claim_id="c1")
        contradicted = score_claim(
            claim, ClaimAdjudication(claim_id="c1", evidence=[make_evidence(Stance.CONTRADICTS)])
        )
        result = score_run([contradicted], {"c1": claim})
        assert result.breakdown["penalties"]["contradiction"] > 0

    def test_partial_page_coverage_incurs_a_penalty(self):
        claim = make_claim(claim_id="c1")
        score = score_claim(claim, None)
        full = score_run([score], {"c1": claim}, pages_analysed=10, pages_total=10)
        partial = score_run([score], {"c1": claim}, pages_analysed=5, pages_total=10)
        assert partial.score < full.score

    def test_deterministic(self):
        claim = make_claim(claim_id="c1")
        score = score_claim(claim, None)
        assert score_run([score], {"c1": claim}).score == score_run([score], {"c1": claim}).score


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

    async def test_unverifiable_quote_downgrades_stance_to_neutral(self):
        """A model that cannot quote the abstract must not move the score."""
        adjudicator = self._adjudicator_returning(
            {
                "adjudications": [
                    {
                        "evidence_ref": "E1",
                        "stance": "supports",
                        "relevance": 0.9,
                        "strength": 0.9,
                        "supporting_quote": "The drug cured every patient in the trial cohort.",
                        "rationale": "r",
                        "caveats": [],
                        "study_design": "randomized_trial",
                    }
                ]
            }
        )
        result = await adjudicator.adjudicate_claim(
            claim_id="c1",
            claim_statement="s",
            claim_quote="q",
            claim_category="mechanism",
            claimed_tier="in_vivo_animal",
            records=[
                ScoredRecord(record=make_record(), relevance=0.9, quality=0.8, rank_score=0.9)
            ],
        )
        assert result.evidence[0].stance is Stance.NEUTRAL
        assert any("could not be verified" in c for c in result.evidence[0].caveats)

    async def test_verifiable_quote_keeps_the_stance(self):
        adjudicator = self._adjudicator_returning(
            {
                "adjudications": [
                    {
                        "evidence_ref": "E1",
                        "stance": "supports",
                        "relevance": 0.9,
                        "strength": 0.85,
                        "supporting_quote": "Dopaminergic neuron loss was significantly reduced compared with vehicle controls",
                        "rationale": "r",
                        "caveats": ["Mouse model only"],
                        "study_design": "preclinical",
                    }
                ]
            }
        )
        result = await adjudicator.adjudicate_claim(
            claim_id="c1",
            claim_statement="s",
            claim_quote="q",
            claim_category="preclinical_efficacy",
            claimed_tier="in_vivo_animal",
            records=[
                ScoredRecord(record=make_record(), relevance=0.9, quality=0.8, rank_score=0.9)
            ],
        )
        assert result.evidence[0].stance is Stance.SUPPORTS
        assert result.evidence[0].weighted_strength > 0

    async def test_retracted_record_is_neutralised(self):
        adjudicator = self._adjudicator_returning(
            {
                "adjudications": [
                    {
                        "evidence_ref": "E1",
                        "stance": "supports",
                        "relevance": 0.9,
                        "strength": 0.9,
                        "supporting_quote": "Dopaminergic neuron loss was significantly reduced",
                        "rationale": "r",
                        "caveats": [],
                        "study_design": "randomized_trial",
                    }
                ]
            }
        )
        record = make_record(publication_types=["Retracted Publication"], is_retracted=True)
        result = await adjudicator.adjudicate_claim(
            claim_id="c1",
            claim_statement="s",
            claim_quote="q",
            claim_category="mechanism",
            claimed_tier="in_vitro",
            records=[ScoredRecord(record=record, relevance=0.9, quality=0.0, rank_score=0.5)],
        )
        assert result.evidence[0].stance is Stance.NEUTRAL
        assert any("retracted" in c.lower() for c in result.evidence[0].caveats)

    async def test_unknown_evidence_reference_is_dropped(self):
        adjudicator = self._adjudicator_returning(
            {
                "adjudications": [
                    {
                        "evidence_ref": "E99",
                        "stance": "supports",
                        "relevance": 0.9,
                        "strength": 0.9,
                        "supporting_quote": "x",
                        "rationale": "r",
                        "caveats": [],
                        "study_design": "review",
                    }
                ]
            }
        )
        result = await adjudicator.adjudicate_claim(
            claim_id="c1",
            claim_statement="s",
            claim_quote="q",
            claim_category="mechanism",
            claimed_tier="in_vitro",
            records=[
                ScoredRecord(record=make_record(), relevance=0.9, quality=0.8, rank_score=0.9)
            ],
        )
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


class TestRules:
    def _context(self, claim: ClaimScoringInput, adjudication=None, **kwargs) -> ClaimContext:
        return ClaimContext(
            claim_id=claim.claim_id,
            statement=kwargs.pop("statement", "NG-101 slows neurodegeneration in patients"),
            page_number=kwargs.pop("page_number", 4),
            scoring=claim,
            score=score_claim(claim, adjudication),
            adjudication=adjudication,
            **kwargs,
        )

    def _rule_ids(self, contexts) -> set[str]:
        return {f.rule_id for f in evaluate(contexts)}

    def test_contradicted_thesis_claim_is_critical(self):
        adjudication = ClaimAdjudication(
            claim_id="clm_1", evidence=[make_evidence(Stance.CONTRADICTS)]
        )
        findings = evaluate([self._context(make_claim(), adjudication)])
        contradicted = [f for f in findings if f.rule_id == "contradicted_thesis_claim"]
        assert contradicted and contradicted[0].severity is RiskSeverity.CRITICAL

    def test_uncorroborated_thesis_claim_is_flagged(self):
        assert "uncorroborated_thesis_claim" in self._rule_ids([self._context(make_claim())])

    def test_clinical_claim_on_preclinical_evidence_flags_translational_gap(self):
        claim = make_claim(
            category=ClaimCategory.CLINICAL_EFFICACY, claimed_tier=EvidenceTier.IN_VIVO_ANIMAL
        )
        assert "translational_gap" in self._rule_ids([self._context(claim)])

    def test_no_translational_gap_for_matched_tier(self):
        claim = make_claim(
            category=ClaimCategory.CLINICAL_EFFICACY, claimed_tier=EvidenceTier.CLINICAL_PHASE_2
        )
        assert "translational_gap" not in self._rule_ids([self._context(claim)])

    def test_assertion_without_data_is_flagged(self):
        claim = make_claim(claimed_tier=EvidenceTier.NONE_STATED, importance=0.9)
        assert "assertion_without_data" in self._rule_ids([self._context(claim)])

    def test_missing_statistics_control_and_n_each_flag(self):
        claim = make_claim(
            has_statistics=False,
            has_comparator=False,
            has_sample_size=False,
            has_effect_size=True,
        )
        ids = self._rule_ids([self._context(claim)])
        assert {"effect_without_statistics", "effect_without_control", "effect_without_n"} <= ids

    def test_complete_statistics_produce_no_rigor_flags(self):
        ids = self._rule_ids([self._context(make_claim())])
        assert "effect_without_statistics" not in ids
        assert "effect_without_control" not in ids

    def test_fuzzy_quote_is_flagged(self):
        claim = make_claim(quote_verification=QuoteVerification.FUZZY)
        assert "approximate_provenance" in self._rule_ids(
            [self._context(claim, quote_match_score=0.9)]
        )

    def test_terminated_trial_precedent_is_surfaced(self):
        trial = make_record(
            source=EvidenceSource.CLINICALTRIALS_GOV,
            external_id="NCT1",
            pmid=None,
            nct_id="NCT1",
        )
        trial.trial = {"status": "TERMINATED", "why_stopped": "Futility"}
        evidence = make_evidence(Stance.NEUTRAL)
        evidence.record = trial
        adjudication = ClaimAdjudication(claim_id="clm_1", evidence=[evidence])
        findings = evaluate([self._context(make_claim(), adjudication)])
        stopped = [f for f in findings if f.rule_id == "terminated_trial_precedent"]
        assert stopped and "Futility" in stopped[0].description

    def test_retracted_evidence_is_surfaced(self):
        retracted = make_record(publication_types=["Retracted Publication"], is_retracted=True)
        evidence = make_evidence(Stance.NEUTRAL)
        evidence.record = retracted
        adjudication = ClaimAdjudication(claim_id="clm_1", evidence=[evidence])
        assert "retracted_evidence" in self._rule_ids([self._context(make_claim(), adjudication)])

    def test_preprint_only_support_is_flagged(self):
        preprint = make_record(is_preprint=True)
        evidence = make_evidence(Stance.SUPPORTS)
        evidence.record = preprint
        adjudication = ClaimAdjudication(claim_id="clm_1", evidence=[evidence])
        assert "support_only_from_preprints" in self._rule_ids(
            [self._context(make_claim(), adjudication)]
        )

    def test_low_literature_coverage_is_a_portfolio_finding(self):
        contexts = [
            self._context(make_claim(claim_id=f"clm_{i}", is_thesis_critical=False))
            for i in range(6)
        ]
        assert "low_literature_coverage" in self._rule_ids(contexts)

    def test_pervasive_hedging_is_a_portfolio_finding(self):
        contexts = [
            self._context(make_claim(claim_id=f"clm_{i}", hedging_language=True, importance=0.8))
            for i in range(4)
        ]
        assert "pervasive_hedging" in self._rule_ids(contexts)

    def test_findings_are_sorted_by_severity(self):
        adjudication = ClaimAdjudication(
            claim_id="clm_1", evidence=[make_evidence(Stance.CONTRADICTS)]
        )
        claim = make_claim(quote_verification=QuoteVerification.FUZZY, has_statistics=False)
        findings = evaluate([self._context(claim, adjudication)])
        order = {
            RiskSeverity.CRITICAL: 0,
            RiskSeverity.HIGH: 1,
            RiskSeverity.MEDIUM: 2,
            RiskSeverity.LOW: 3,
            RiskSeverity.INFO: 4,
        }
        ranks = [order[f.severity] for f in findings]
        assert ranks == sorted(ranks)

    def test_no_duplicate_rule_per_claim(self):
        findings = evaluate([self._context(make_claim())] * 2)
        keys = [(f.rule_id, f.claim_id) for f in findings]
        assert len(keys) == len(set(keys))

    def test_empty_input_produces_nothing(self):
        assert evaluate([]) == []
