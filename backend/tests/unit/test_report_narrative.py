"""Report quality: explanations that cannot drift from the numbers.

Measured on the CRISPR memo before this change:

    section                          words  bullets  sentences  avg sentence
    Scientific Thesis                  295        0          5          59.0
    Evidence Base and Its Limits       226        0          5          45.2
    Contradictions and Unsupported     245        0          7          35.0

    "Evidence Base" vs "Contradictions": 32 of 42 claims shared (Jaccard 0.76)
    "BioIntel assesses that": 15 occurrences

Four sections restating the same findings, in 50-word sentences, with no
bullets. The prose is the model's job; these tests cover the part that is
not -- the computed explanations, the ranking, and the structure they are
rendered into.
"""

from __future__ import annotations

import pytest

from app.analysis.corroboration import CorroborationAssessment
from app.analysis.questions import Question
from app.analysis.scorecard import ScorecardInput, build_scorecard
from app.analysis.scoring import ClaimScoringInput, score_claim
from app.core.enums import (
    ClaimCategory,
    ClaimType,
    CorroborationStatus,
    EvidenceState,
    EvidenceTier,
    QuestionPriority,
    QuoteVerification,
    RiskCategory,
)
from app.reporting.builder import SECTION_PLAN, _format_section_routing, _render_executive_summary
from app.reporting.narrative import (
    confidence_reasons,
    dimension_narratives,
    evidence_ledger,
    rank_questions,
    recommendation_drivers,
)


def make_summary(
    ref: str,
    state: EvidenceState,
    *,
    thesis_critical: bool = False,
    importance: float = 0.7,
    requires_audit: bool = False,
) -> dict:
    return {
        "ref": ref,
        "claim_id": ref,
        "statement": f"statement for {ref}",
        "importance": importance,
        "is_thesis_critical": thesis_critical,
        "credibility_score": 60.0,
        "evidence_state": state.value,
        "requires_audit": requires_audit or state is EvidenceState.COMPANY_REPORTED,
        "contradicted": state is EvidenceState.CONTRADICTED,
        "retrieval_status": "none" if state is EvidenceState.PLAUSIBLE_UNVERIFIED else "found",
    }


def make_scorecard_input(claim_id: str, claim_type: ClaimType, status: CorroborationStatus):
    scoring = ClaimScoringInput(
        claim_id=claim_id,
        claim_type=claim_type,
        category=ClaimCategory.MECHANISM,
        claimed_tier=EvidenceTier.NONE_STATED,
        importance=0.8,
        is_thesis_critical=True,
        hedging_language=False,
        quote_verification=QuoteVerification.EXACT,
        extraction_confidence=0.8,
    )
    corroboration = CorroborationAssessment(
        claim_id=claim_id, status=status, confidence=0.5, rationale="fixture"
    )
    return ScorecardInput(
        claim_id=claim_id,
        statement=f"statement {claim_id}",
        scoring=scoring,
        score=score_claim(scoring, corroboration),
        corroboration=corroboration,
    )


# ================================================= goal 1: no redundancy ===
class TestSectionRemitsAreDisjoint:
    def test_every_section_declares_what_it_owns(self):
        for section in SECTION_PLAN:
            assert section.get("owns"), f"{section['id']} does not declare its remit"

    def test_every_section_declares_what_it_must_not_repeat(self):
        """The 0.76 overlap came from sections with no boundary."""
        overlapping = {"evidence_base", "literature", "contradictions", "translational"}
        for section in SECTION_PLAN:
            if section["id"] in overlapping:
                assert "DO NOT" in section["instruction"], section["id"]

    def test_owned_remits_are_unique(self):
        remits = [s["owns"] for s in SECTION_PLAN]
        assert len(remits) == len(set(remits))

    def test_contested_claims_are_routed_to_one_section(self):
        summaries = [
            make_summary("C1", EvidenceState.CONTRADICTED, thesis_critical=True),
            make_summary("C2", EvidenceState.PLAUSIBLE_UNVERIFIED, thesis_critical=True),
            make_summary("C3", EvidenceState.VERIFIED),
        ]
        routing = _format_section_routing(summaries)
        assert "[C1]" in routing and "[C2]" in routing
        # A corroborated claim is not a contested one and must not appear.
        assert "[C3]" not in routing
        assert "ONLY claims" in routing

    def test_routing_says_so_when_nothing_is_contested(self):
        routing = _format_section_routing([make_summary("C1", EvidenceState.VERIFIED)])
        assert "none" in routing.lower()


# ============================================ goal 2: score explanations ===
class TestEveryScoreExplainsItself:
    def test_each_assessed_dimension_gets_countable_drivers(self):
        inputs = [
            make_scorecard_input(f"c{i}", ClaimType.MECHANISM, CorroborationStatus.CORROBORATED)
            for i in range(4)
        ]
        card = build_scorecard(inputs, pipeline_size=3)
        summaries = [make_summary(f"c{i}", EvidenceState.VERIFIED) for i in range(4)]

        for narrative in dimension_narratives(card, summaries):
            assert narrative.drivers, f"{narrative.dimension} scored with no explanation"

    def test_drivers_count_findings_rather_than_asserting(self):
        inputs = [
            make_scorecard_input("c1", ClaimType.MECHANISM, CorroborationStatus.CORROBORATED),
            make_scorecard_input(
                "c2", ClaimType.MECHANISM, CorroborationStatus.INSUFFICIENT_EVIDENCE
            ),
        ]
        card = build_scorecard(inputs, pipeline_size=2)
        summaries = [
            make_summary("c1", EvidenceState.VERIFIED),
            make_summary("c2", EvidenceState.PLAUSIBLE_UNVERIFIED),
        ]
        narratives = {n.dimension: n for n in dimension_narratives(card, summaries)}
        science = narratives["scientific_validity"]
        joined = " ".join(science.drivers)
        assert any(char.isdigit() for char in joined), "drivers must be countable"
        assert "claim(s) informed this dimension" in joined

    def test_no_contradictions_is_stated_as_a_finding(self):
        inputs = [make_scorecard_input("c1", ClaimType.MECHANISM, CorroborationStatus.CORROBORATED)]
        card = build_scorecard(inputs, pipeline_size=1)
        narratives = dimension_narratives(card, [make_summary("c1", EvidenceState.VERIFIED)])
        science = next(n for n in narratives if n.dimension == "scientific_validity")
        assert any("no biological" in d for d in science.drivers)

    def test_unassessed_dimensions_explain_that_too(self):
        card = build_scorecard(
            [make_scorecard_input("c1", ClaimType.MECHANISM, CorroborationStatus.CORROBORATED)],
            pipeline_size=0,
        )
        narratives = [n for n in dimension_narratives(card, []) if not n.assessed]
        assert narratives
        for narrative in narratives:
            assert narrative.drivers
            assert "not an adverse finding" in " ".join(narrative.drivers)

    def test_structural_dimensions_are_not_explained_by_evidence_language(self):
        """Disclosure quality asks if the deck is checkable, not what checking found."""
        inputs = [
            make_scorecard_input(f"c{i}", ClaimType.MECHANISM, CorroborationStatus.CORROBORATED)
            for i in range(3)
        ]
        card = build_scorecard(inputs, pipeline_size=3)
        narratives = {n.dimension: n for n in dimension_narratives(card, [])}
        disclosure = narratives["disclosure_quality"]
        assert "unaffected by verification coverage" in " ".join(disclosure.drivers)
        assert "checkability" in disclosure.what_would_move_it


# ============================================ goal 4: confidence reasons ===
class TestConfidenceIsExplainedSeparately:
    def test_low_coverage_is_named_as_the_reason(self):
        ledger = evidence_ledger(
            [make_summary(f"c{i}", EvidenceState.PLAUSIBLE_UNVERIFIED) for i in range(10)]
        )
        reasons = confidence_reasons(ledger, None, [])
        assert any("verification coverage" in r.lower() for r in reasons)

    def test_proprietary_data_is_named_as_the_reason(self):
        ledger = evidence_ledger(
            [make_summary(f"c{i}", EvidenceState.COMPANY_REPORTED) for i in range(6)]
        )
        reasons = confidence_reasons(ledger, None, [])
        assert any("proprietary" in r.lower() for r in reasons)

    def test_failed_retrieval_is_attributed_to_us_not_the_company(self):
        summaries = [make_summary(f"c{i}", EvidenceState.PLAUSIBLE_UNVERIFIED) for i in range(4)]
        ledger = evidence_ledger(summaries)
        reasons = " ".join(confidence_reasons(ledger, None, []))
        assert "retrieval returned nothing" in reasons

    def test_good_coverage_says_so_rather_than_inventing_a_problem(self):
        ledger = evidence_ledger([make_summary(f"c{i}", EvidenceState.VERIFIED) for i in range(8)])
        reasons = confidence_reasons(ledger, None, [])
        assert reasons
        assert any("sufficient" in r.lower() for r in reasons)

    def test_confidence_reasons_never_comment_on_the_science(self):
        """Confidence is about what we could check, credibility about the science."""
        ledger = evidence_ledger(
            [make_summary(f"c{i}", EvidenceState.PLAUSIBLE_UNVERIFIED) for i in range(6)]
        )
        for reason in confidence_reasons(ledger, None, []):
            assert "weak science" not in reason.lower()
            assert "poor" not in reason.lower()


# ======================================== goal 5: executive summary shape ===
class TestExecutiveSummaryStructure:
    def test_renders_the_five_ic_blocks(self):
        class Summary:
            investment_thesis = "The bet is that base editing durably corrects the defect."
            key_strengths = ["Approved product [C1]", "Registry-confirmed Phase 3 [C2]"]
            key_risks = ["One stage discrepancy [C7]", "Platform claims unaudited [C9]"]
            recommendation_line = "Advance with conditions, subject to the stage discrepancy."
            diligence_priorities = ["Request X", "Request Y", "Request Z"]

        rendered = _render_executive_summary(Summary())
        assert "The bet is that base editing" in rendered
        assert "**What is established**" in rendered
        assert "**What is at risk or unresolved**" in rendered
        assert "**Recommendation.**" in rendered
        assert "**Do these three things first**" in rendered
        assert "1. Request X" in rendered

    def test_summary_is_bulleted_not_a_prose_wall(self):
        class Summary:
            investment_thesis = "Thesis."
            key_strengths = ["a", "b", "c"]
            key_risks = ["d", "e"]
            recommendation_line = "Advance."
            diligence_priorities = ["x", "y", "z"]

        rendered = _render_executive_summary(Summary())
        bullets = [ln for ln in rendered.splitlines() if ln.startswith(("-", "1.", "2.", "3."))]
        assert len(bullets) >= 8

    def test_a_plain_string_summary_still_renders(self):
        assert _render_executive_summary("legacy prose") == "legacy prose"


# =========================================== goal 6: ranked top questions ===
class TestQuestionsAreRankedByImpact:
    def _question(self, text, priority, claim_ids):
        return Question(
            question=text,
            rationale="because",
            priority=priority,
            category=RiskCategory.SCIENTIFIC,
            what_good_looks_like="the dataset",
            claim_ids=claim_ids,
        )

    def test_only_five_are_returned(self):
        summaries = [make_summary(f"c{i}", EvidenceState.PLAUSIBLE_UNVERIFIED) for i in range(10)]
        questions = [self._question(f"q{i}", QuestionPriority.MEDIUM, [f"c{i}"]) for i in range(12)]
        assert len(rank_questions(questions, summaries)) == 5

    def test_a_contradiction_outranks_an_unverified_claim(self):
        summaries = [
            make_summary("c1", EvidenceState.CONTRADICTED, thesis_critical=True),
            make_summary("c2", EvidenceState.PLAUSIBLE_UNVERIFIED),
        ]
        questions = [
            self._question("about the unverified claim", QuestionPriority.HIGH, ["c2"]),
            self._question("about the contradiction", QuestionPriority.HIGH, ["c1"]),
        ]
        ranked = rank_questions(questions, summaries)
        assert ranked[0][0].question == "about the contradiction"

    def test_breadth_never_outranks_severity(self):
        """Five settled claims must not beat one contradicted thesis claim."""
        summaries = [make_summary("bad", EvidenceState.CONTRADICTED, thesis_critical=True)]
        summaries += [make_summary(f"ok{i}", EvidenceState.VERIFIED) for i in range(5)]
        questions = [
            self._question("broad", QuestionPriority.HIGH, [f"ok{i}" for i in range(5)]),
            self._question("narrow but decisive", QuestionPriority.HIGH, ["bad"]),
        ]
        ranked = rank_questions(questions, summaries)
        assert ranked[0][0].question == "narrow but decisive"

    def test_unattached_questions_rank_below_attached_ones(self):
        summaries = [make_summary("c1", EvidenceState.PLAUSIBLE_UNVERIFIED, thesis_critical=True)]
        questions = [
            self._question("generic", QuestionPriority.HIGH, []),
            self._question("specific", QuestionPriority.HIGH, ["c1"]),
        ]
        ranked = rank_questions(questions, summaries)
        assert ranked[0][0].question == "specific"

    def test_each_question_says_why_it_ranks_there(self):
        summaries = [make_summary("c1", EvidenceState.COMPANY_REPORTED, thesis_critical=True)]
        questions = [self._question("audit it", QuestionPriority.HIGH, ["c1"])]
        ((_, why),) = rank_questions(questions, summaries)
        assert "audits a company-reported figure" in why


# ========================================= goal 7: evidence traceability ===
class TestRecommendationIsTraceable:
    def test_drivers_name_the_evidence_states_that_decided_it(self):
        inputs = [
            make_scorecard_input(f"c{i}", ClaimType.MECHANISM, CorroborationStatus.CORROBORATED)
            for i in range(4)
        ]
        card = build_scorecard(inputs, pipeline_size=3)
        summaries = [make_summary(f"c{i}", EvidenceState.VERIFIED) for i in range(4)]
        drivers = recommendation_drivers(card, evidence_ledger(summaries), card.overall_score)

        joined = " ".join(drivers)
        assert "verification coverage" in joined
        assert "science scores" in joined
        assert "confidence" in joined

    def test_contradictions_appear_in_the_trace(self):
        inputs = [make_scorecard_input("c1", ClaimType.MECHANISM, CorroborationStatus.CONTRADICTED)]
        card = build_scorecard(inputs, pipeline_size=1)
        summaries = [make_summary("c1", EvidenceState.CONTRADICTED, thesis_critical=True)]
        drivers = " ".join(recommendation_drivers(card, evidence_ledger(summaries), 50.0))
        assert "contradiction" in drivers

    def test_absence_of_contradiction_is_stated_positively(self):
        inputs = [
            make_scorecard_input(
                "c1", ClaimType.MECHANISM, CorroborationStatus.INSUFFICIENT_EVIDENCE
            )
        ]
        card = build_scorecard(inputs, pipeline_size=1)
        summaries = [make_summary("c1", EvidenceState.PLAUSIBLE_UNVERIFIED)]
        drivers = " ".join(recommendation_drivers(card, evidence_ledger(summaries), 58.0))
        assert "no claim contradicted" in drivers

    def test_non_biomedical_trace_says_the_framework_does_not_apply(self):
        inputs = [
            make_scorecard_input(
                f"c{i}", ClaimType.TRACK_RECORD, CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
            )
            for i in range(10)
        ]
        card = build_scorecard(inputs, pipeline_size=2)
        summaries = [make_summary(f"c{i}", EvidenceState.COMPANY_REPORTED) for i in range(10)]
        drivers = " ".join(recommendation_drivers(card, evidence_ledger(summaries), 50.0))
        assert "do not apply" in drivers


class TestEvidenceLedger:
    def test_counts_reconcile_with_the_claims(self):
        summaries = [
            make_summary("c1", EvidenceState.VERIFIED),
            make_summary("c2", EvidenceState.PARTIALLY_VERIFIED),
            make_summary("c3", EvidenceState.PLAUSIBLE_UNVERIFIED),
            make_summary("c4", EvidenceState.COMPANY_REPORTED),
            make_summary("c5", EvidenceState.CONTRADICTED),
        ]
        ledger = evidence_ledger(summaries)
        assert ledger["claims_scored"] == 5
        assert ledger["verified"] == 1
        assert ledger["partially_verified"] == 1
        assert ledger["contradicted"] == 1
        assert ledger["verification_coverage"] == pytest.approx(0.4)

    def test_empty_input_does_not_divide_by_zero(self):
        ledger = evidence_ledger([])
        assert ledger["claims_scored"] == 0
        assert ledger["verification_coverage"] == 0.0
