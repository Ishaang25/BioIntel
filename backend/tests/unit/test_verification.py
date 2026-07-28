"""Authoritative verification, alias normalisation and the scorecard.

These cover the machinery added after the Moderna review: routing regulatory
and pipeline claims to sources that can actually settle them, expanding
entity aliases so retrieval stops producing false negatives, and turning the
claim-level results into a scorecard an investment committee can act on.
"""

from __future__ import annotations

import httpx
import pytest

from app.analysis.claim_policy import (
    POLICIES,
    corroboration_guidance,
    is_scorable,
    policy_for,
)
from app.analysis.corroboration import CorroborationAssessment
from app.analysis.scorecard import ScorecardInput, build_scorecard, detect_archetype
from app.analysis.scoring import ClaimScoringInput, score_claim
from app.analysis.verification import (
    RegulatoryVerifier,
    VerificationRequest,
    extract_asserted_phase,
)
from app.core.enums import (
    ClaimCategory,
    ClaimType,
    CompanyArchetype,
    CorroborationStatus,
    EvidenceTier,
    ICRecommendation,
    QuoteVerification,
    ScoreDimension,
    VerificationStatus,
)
from app.evidence.clinicaltrials import ClinicalTrialsClient
from app.evidence.normalization import (
    build_or_clause,
    expand_query_terms,
    normalize_company,
    normalize_drug,
    normalize_gene,
    normalize_indication,
)
from app.evidence.openfda import OpenFDAClient

# ------------------------------------------------------------- fixtures ---
DRUGSFDA_HIT = {
    "results": [
        {
            "application_number": "BLA125514",
            "sponsor_name": "MERCK SHARP DOHME",
            "openfda": {"brand_name": ["KEYTRUDA"], "generic_name": ["pembrolizumab"]},
            "products": [{"brand_name": "KEYTRUDA", "marketing_status": "Prescription"}],
            "submissions": [
                {
                    "submission_status": "AP",
                    "submission_status_date": "20140904",
                    "submission_type": "ORIG",
                }
            ],
        }
    ]
}

CTGOV_PHASE1 = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT04159103", "briefTitle": "Phase 1/2 study"},
                "statusModule": {
                    "overallStatus": "RECRUITING",
                    "startDateStruct": {"date": "2021-01-01"},
                },
                "designModule": {"phases": ["PHASE1", "PHASE2"], "enrollmentInfo": {"count": 24}},
                "descriptionModule": {"briefSummary": "A dose-optimisation study."},
                "conditionsModule": {"conditions": ["Propionic Acidemia"]},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "ModernaTX"}},
            },
            "hasResults": False,
        }
    ]
}

CTGOV_PHASE3 = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT05085366", "briefTitle": "Phase 3 study"},
                "statusModule": {
                    "overallStatus": "ACTIVE_NOT_RECRUITING",
                    "startDateStruct": {"date": "2021-10-01"},
                },
                "designModule": {"phases": ["PHASE3"], "enrollmentInfo": {"count": 7000}},
                "descriptionModule": {"briefSummary": "A pivotal efficacy study."},
                "conditionsModule": {"conditions": ["Cytomegalovirus Infections"]},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "ModernaTX"}},
            },
            "hasResults": False,
        }
    ]
}


def verifier_with(handler) -> RegulatoryVerifier:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RegulatoryVerifier(
        openfda=OpenFDAClient(http), clinicaltrials=ClinicalTrialsClient(http)
    )


# ================================================== regulatory verification ===
class TestApprovalVerification:
    async def test_matching_approval_is_verified(self):
        verifier = verifier_with(lambda r: httpx.Response(200, json=DRUGSFDA_HIT))
        result = await verifier.verify(
            VerificationRequest(
                claim_id="c1",
                claim_type=ClaimType.REGULATORY_APPROVAL,
                statement="KEYTRUDA is FDA approved",
                product_names=["KEYTRUDA", "pembrolizumab"],
            )
        )
        assert result.status is VerificationStatus.VERIFIED
        assert result.corroboration is CorroborationStatus.CORROBORATED
        assert "BLA125514" in result.identifiers

    async def test_missing_vaccine_is_unverified_not_refuted(self):
        """The exact Moderna case.

        Drugs@FDA does not index CBER-licensed vaccines, so mRESVIA returns
        nothing. That must read as a coverage gap, never as evidence against
        an approval that genuinely exists.
        """
        verifier = verifier_with(lambda r: httpx.Response(404, json={}))
        result = await verifier.verify(
            VerificationRequest(
                claim_id="c1",
                claim_type=ClaimType.REGULATORY_APPROVAL,
                statement="mRESVIA has FDA approval for adults 60+",
                product_names=["mRESVIA"],
            )
        )
        assert result.status is VerificationStatus.NOT_FOUND
        assert result.corroboration is CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
        assert result.status is not VerificationStatus.REFUTED
        assert "does not index CBER-licensed vaccines" in result.detail

    async def test_non_vaccine_miss_explains_the_likely_cause(self):
        verifier = verifier_with(lambda r: httpx.Response(404, json={}))
        result = await verifier.verify(
            VerificationRequest(
                claim_id="c1",
                claim_type=ClaimType.REGULATORY_APPROVAL,
                statement="NG-101 is approved",
                product_names=["NG-101"],
            )
        )
        assert result.corroboration is CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED
        assert "naming mismatch" in result.detail


class TestPipelineStageVerification:
    async def test_registry_confirms_the_asserted_phase(self):
        verifier = verifier_with(lambda r: httpx.Response(200, json=CTGOV_PHASE3))
        result = await verifier.verify(
            VerificationRequest(
                claim_id="c1",
                claim_type=ClaimType.PIPELINE_STAGE,
                statement="CMV: Phase 3 efficacy",
                product_names=["mRNA-1647"],
                asserted_phase="3",
            )
        )
        assert result.status is VerificationStatus.VERIFIED
        assert "NCT05085366" in result.identifiers

    async def test_registry_disagreement_is_a_genuine_refutation(self):
        """A real finding the old system could not distinguish from a miss."""
        verifier = verifier_with(lambda r: httpx.Response(200, json=CTGOV_PHASE1))
        result = await verifier.verify(
            VerificationRequest(
                claim_id="c1",
                claim_type=ClaimType.PIPELINE_STAGE,
                statement="PA: registrational study",
                product_names=["mRNA-3927"],
                asserted_phase="3",
            )
        )
        assert result.status is VerificationStatus.REFUTED
        assert result.corroboration is CorroborationStatus.CONTRADICTED
        assert "Phase 2" in result.detail or "Phase 1" in result.detail

    async def test_no_registered_trial_is_not_a_refutation(self):
        verifier = verifier_with(lambda r: httpx.Response(200, json={"studies": []}))
        result = await verifier.verify(
            VerificationRequest(
                claim_id="c1",
                claim_type=ClaimType.PIPELINE_STAGE,
                statement="Phase 3 ongoing",
                product_names=["XYZ-1"],
                asserted_phase="3",
            )
        )
        assert result.status is VerificationStatus.NOT_FOUND
        assert result.corroboration is CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED

    async def test_source_failure_degrades_to_unverified(self):
        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network down")

        verifier = verifier_with(explode)
        results = await verifier.verify_many(
            [
                VerificationRequest(
                    claim_id="c1",
                    claim_type=ClaimType.REGULATORY_APPROVAL,
                    statement="approved",
                    product_names=["X"],
                )
            ]
        )
        assert results["c1"].corroboration is CorroborationStatus.NOT_INDEPENDENTLY_VERIFIED


class TestPhaseParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("CMV: Phase 3 efficacy", "3"),
            ("a Phase III pivotal trial", "3"),
            ("Phase I/II dose escalation", "1/2"),
            ("Phase 2b study", "2"),
            ("no phase mentioned here", None),
        ],
    )
    def test_asserted_phase(self, text, expected):
        assert extract_asserted_phase(text) == expected


# ============================================================ normalisation ===
class TestAliasNormalisation:
    def test_asset_code_reaches_the_brand_name(self):
        """The retrieval false-negative that produced 'unsupported' verdicts."""
        terms = [t.lower() for t in expand_query_terms(["mRNA-1345"])]
        assert any("mresvia" in t for t in terms)

    def test_brand_name_reaches_the_asset_code(self):
        terms = [t.lower() for t in expand_query_terms(["mRESVIA"])]
        assert any("mrna-1345" in t for t in terms)

    def test_gene_aliases_resolve_to_the_official_symbol(self):
        assert normalize_gene("K-ras").canonical == "KRAS"
        assert normalize_gene("HER2").canonical == "ERBB2"
        assert "HER-2" in normalize_gene("HER2").aliases

    def test_indication_abbreviations_expand(self):
        assert "propionic acidemia" in normalize_indication("PA").search_terms
        assert "PA" in normalize_indication("propionic acidemia").search_terms

    def test_company_suffixes_do_not_break_matching(self):
        assert normalize_company("Moderna, Inc.").canonical == "moderna"
        assert normalize_company("ModernaTX").canonical == "moderna"

    def test_unknown_company_still_yields_a_bare_name(self):
        assert "NeuroGen" in normalize_company("NeuroGen Therapeutics, Inc.").search_terms

    def test_unknown_asset_gets_punctuation_variants(self):
        terms = expand_query_terms(["NG-101"])
        assert "NG-101" in terms
        assert any(t.replace(" ", "") == "NG101" for t in terms)

    def test_or_clause_is_valid_pubmed_syntax(self):
        clause = build_or_clause(["mRNA-1345", "mRESVIA"], field="tiab")
        assert clause.startswith("(") and clause.endswith(")")
        assert " OR " in clause
        assert '"mRNA-1345"[tiab]' in clause

    def test_single_term_needs_no_parentheses(self):
        assert build_or_clause(["KRAS"], field="tiab") == '"KRAS"[tiab]'

    def test_empty_input_is_safe(self):
        assert build_or_clause([]) == ""
        assert expand_query_terms([]) == []

    def test_drug_normalisation_is_symmetric(self):
        for name in ("mRNA-4157", "V940", "intismeran autogene"):
            assert normalize_drug(name).canonical == "mRNA-4157"


# =========================================================== claim policy ===
class TestClaimPolicy:
    def test_every_claim_type_has_a_policy(self):
        for claim_type in ClaimType:
            assert claim_type in POLICIES, claim_type

    def test_unscorable_types_are_the_expected_set(self):
        unscorable = {ct for ct in ClaimType if not is_scorable(ct)}
        assert unscorable == {
            ClaimType.MARKETING,
            ClaimType.CORPORATE_VISION,
            ClaimType.STRATEGIC_OBJECTIVE,
            ClaimType.FORWARD_LOOKING,
            ClaimType.FINANCIAL_GUIDANCE,
            ClaimType.MARKET_ESTIMATE,
        }

    def test_priors_are_ordered_sensibly(self):
        """A public matter of record outranks an untested hypothesis."""
        assert (
            policy_for(ClaimType.REGULATORY_APPROVAL).prior
            > policy_for(ClaimType.PIPELINE_STAGE).prior
            > policy_for(ClaimType.CLINICAL_RESULT).prior
            > policy_for(ClaimType.MECHANISM).prior
        )

    def test_priors_and_leverage_are_in_range(self):
        for claim_type, policy in POLICIES.items():
            assert 0.0 <= policy.prior <= 1.0, claim_type
            assert 0.0 <= policy.evidence_leverage <= 1.0, claim_type

    def test_scorable_types_never_null_to_an_adverse_status(self):
        """A null result must never itself be a finding against the company."""
        for claim_type, policy in POLICIES.items():
            if not policy.scorable:
                continue
            assert policy.null_result_status not in (
                CorroborationStatus.CONTRADICTED,
                CorroborationStatus.DISPUTED,
            ), claim_type

    def test_guidance_names_the_right_source(self):
        assert "regulator" in corroboration_guidance(ClaimType.REGULATORY_APPROVAL)
        assert "literature" in corroboration_guidance(ClaimType.MECHANISM)
        assert "cannot be corroborated" in corroboration_guidance(ClaimType.MARKETING)

    def test_unknown_type_falls_back_safely(self):
        assert policy_for("not_a_real_type").claim_type is ClaimType.OTHER


# =============================================================== scorecard ===
def make_input(
    claim_id: str,
    claim_type: ClaimType,
    status: CorroborationStatus,
    *,
    category: ClaimCategory = ClaimCategory.CLINICAL_EFFICACY,
    critical: bool = True,
    tier: EvidenceTier = EvidenceTier.NONE_STATED,
) -> ScorecardInput:
    scoring = ClaimScoringInput(
        claim_id=claim_id,
        claim_type=claim_type,
        category=category,
        claimed_tier=tier,
        importance=0.85,
        is_thesis_critical=critical,
        hedging_language=False,
        quote_verification=QuoteVerification.EXACT,
        extraction_confidence=0.9,
    )
    corroboration = CorroborationAssessment(claim_id=claim_id, status=status, confidence=0.7)
    return ScorecardInput(
        claim_id=claim_id,
        statement=f"statement for {claim_id}",
        scoring=scoring,
        score=score_claim(scoring, corroboration),
        corroboration=corroboration,
    )


class TestScorecard:
    def test_produces_every_dimension(self):
        card = build_scorecard(
            [make_input("c1", ClaimType.CLINICAL_RESULT, CorroborationStatus.CORROBORATED)],
            pipeline_size=3,
        )
        assert {d.dimension for d in card.dimensions} == set(ScoreDimension)

    def test_dimensions_without_claims_are_not_assessed_not_zero(self):
        card = build_scorecard(
            [make_input("c1", ClaimType.MECHANISM, CorroborationStatus.CORROBORATED)],
            pipeline_size=0,
        )
        unassessed = [d for d in card.dimensions if not d.assessed]
        assert unassessed
        for dimension in unassessed:
            assert dimension.score is None
            assert (
                "not a negative finding" in dimension.rationale.lower()
                or "not assessed" in dimension.rationale.lower()
            )

    def test_every_assessed_dimension_explains_itself(self):
        card = build_scorecard(
            [
                make_input("c1", ClaimType.CLINICAL_RESULT, CorroborationStatus.CORROBORATED),
                make_input("c2", ClaimType.REGULATORY_APPROVAL, CorroborationStatus.CORROBORATED),
            ],
            pipeline_size=4,
        )
        for dimension in card.assessed_dimensions():
            assert dimension.rationale
            assert dimension.question

    def test_contradicted_thesis_claim_forces_significant_concerns(self):
        card = build_scorecard(
            [
                make_input("c1", ClaimType.CLINICAL_RESULT, CorroborationStatus.CORROBORATED),
                make_input("c2", ClaimType.PIPELINE_STAGE, CorroborationStatus.CONTRADICTED),
            ],
            pipeline_size=5,
        )
        assert card.recommendation is ICRecommendation.SIGNIFICANT_CONCERNS
        assert "thesis-critical" in card.recommendation_rationale

    def test_unchecked_claims_read_as_an_information_problem(self):
        card = build_scorecard(
            [
                make_input(f"c{i}", ClaimType.MECHANISM, CorroborationStatus.INSUFFICIENT_EVIDENCE)
                for i in range(4)
            ],
            pipeline_size=2,
        )
        assert card.recommendation in (
            ICRecommendation.FURTHER_DILIGENCE_REQUIRED,
            ICRecommendation.ADVANCE_WITH_CONDITIONS,
        )
        assert (
            "not evidence against" in card.recommendation_rationale
            or "information" in card.recommendation_rationale
        )

    def test_scores_are_bounded(self):
        for status in CorroborationStatus:
            card = build_scorecard(
                [make_input("c1", ClaimType.CLINICAL_RESULT, status)], pipeline_size=3
            )
            assert 0.0 <= card.overall_score <= 100.0
            for dimension in card.assessed_dimensions():
                assert 0.0 <= (dimension.score or 0.0) <= 100.0

    def test_pipeline_diversification_rewards_breadth(self):
        claim = [make_input("c1", ClaimType.CLINICAL_RESULT, CorroborationStatus.CORROBORATED)]
        narrow = build_scorecard(claim, pipeline_size=1).by_dimension()[
            ScoreDimension.PIPELINE_DIVERSIFICATION.value
        ]
        broad = build_scorecard(claim, pipeline_size=9).by_dimension()[
            ScoreDimension.PIPELINE_DIVERSIFICATION.value
        ]
        assert (broad.score or 0) > (narrow.score or 0)

    def test_serialises_for_persistence(self):
        card = build_scorecard(
            [make_input("c1", ClaimType.CLINICAL_RESULT, CorroborationStatus.CORROBORATED)],
            pipeline_size=3,
        )
        payload = card.to_dict()
        assert payload["recommendation"]
        assert len(payload["dimensions"]) == len(ScoreDimension)
        assert payload["dimensions"][0]["question"]


class TestArchetypeDetection:
    def test_approved_product_means_commercial_stage(self):
        archetype = detect_archetype(
            [
                make_input("c1", ClaimType.REGULATORY_APPROVAL, CorroborationStatus.CORROBORATED),
                make_input("c2", ClaimType.CLINICAL_RESULT, CorroborationStatus.CORROBORATED),
            ]
        )
        assert archetype is CompanyArchetype.COMMERCIAL_STAGE

    def test_many_platform_claims_and_a_broad_pipeline_means_platform(self):
        inputs = [
            make_input(
                f"p{i}",
                ClaimType.PLATFORM_CAPABILITY,
                CorroborationStatus.PLAUSIBLE_UNVERIFIED,
                category=ClaimCategory.PLATFORM,
            )
            for i in range(4)
        ]
        # A platform *biotech* always names some biology alongside the platform
        # story; "we have a platform" on its own is a claim a software company
        # makes too, and is not by itself evidence of a life-sciences company.
        inputs.append(
            make_input("m1", ClaimType.MECHANISM, CorroborationStatus.PLAUSIBLE_UNVERIFIED)
        )
        assert detect_archetype(inputs, pipeline_size=6) is CompanyArchetype.PLATFORM

    def test_preclinical_only_company_is_detected(self):
        inputs = [
            make_input(
                "c1",
                ClaimType.PRECLINICAL_RESULT,
                CorroborationStatus.INSUFFICIENT_EVIDENCE,
                tier=EvidenceTier.IN_VIVO_ANIMAL,
            )
        ]
        assert detect_archetype(inputs) is CompanyArchetype.PRECLINICAL_ASSET

    def test_archetype_changes_dimension_weighting(self):
        """A platform company's thesis is not scored like a single asset's."""
        inputs = [
            make_input(
                "c1",
                ClaimType.PLATFORM_CAPABILITY,
                CorroborationStatus.CORROBORATED,
                category=ClaimCategory.PLATFORM,
            ),
            make_input("c2", ClaimType.CLINICAL_RESULT, CorroborationStatus.INSUFFICIENT_EVIDENCE),
        ]
        platform = build_scorecard(inputs, archetype=CompanyArchetype.PLATFORM, pipeline_size=8)
        asset = build_scorecard(
            inputs, archetype=CompanyArchetype.CLINICAL_STAGE_ASSET, pipeline_size=8
        )
        assert platform.overall_score != asset.overall_score
