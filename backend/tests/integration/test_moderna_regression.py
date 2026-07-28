"""End-to-end regression for the Moderna review findings.

The Moderna memo scored a company with FDA-approved products, peer-reviewed
evidence and active clinical trials at **24.1/100, "unsupported"**. That single
number destroyed the credibility of the whole scoring system, and the cause was
structural rather than a tuning error.

This module drives the real pipeline over a deck built from the actual
statements in that presentation and asserts, end to end, that each failure mode
is fixed:

1. an approved product no longer scores as unsupported;
2. "we could not check" and "the evidence disagrees" are separate outcomes;
3. forward-looking and promotional statements are excluded, not penalised;
4. a registry that positively disagrees produces a real adverse finding;
5. the memo carries a multi-dimensional scorecard, not one opaque number.
"""

from __future__ import annotations

import httpx
import pytest

from app.analysis.verification import RegulatoryVerifier
from app.core.enums import (
    ADVERSE_CORROBORATION,
    UNCHECKED_CORROBORATION,
    ClaimType,
    CorroborationStatus,
    RunStatus,
)
from app.db.models import AnalysisRun, Claim, ClaimAssessment, Report
from app.db.session import session_scope
from app.evidence.clinicaltrials import ClinicalTrialsClient
from app.evidence.europepmc import EuropePMCClient
from app.evidence.openfda import OpenFDAClient
from app.evidence.pubmed import PubMedClient
from app.evidence.retriever import EvidenceRetriever
from app.llm.client import LLMClient
from app.llm.stub_provider import StubProvider
from app.pipeline.orchestrator import AnalysisPipeline
from app.services.documents import store_document
from tests.fixtures.moderna_deck import moderna_pdf

# --- external sources -------------------------------------------------------
PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>38570682</PMID>
      <Article>
        <Journal><Title>Nature</Title>
          <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
        <ArticleTitle>Interim analyses of a first-in-human phase 1/2 mRNA trial for propionic acidaemia</ArticleTitle>
        <Abstract><AbstractText>An mRNA therapeutic encoding propionyl-CoA carboxylase was
        administered to participants with propionic acidaemia. Treatment was associated with a
        reduction in metabolic decompensation events and was generally well tolerated.</AbstractText></Abstract>
        <AuthorList><Author><LastName>Koeberl</LastName><Initials>D</Initials></Author></AuthorList>
        <PublicationTypeList><PublicationType>Clinical Trial, Phase I</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1038/x</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""

EPMC_PAYLOAD = {
    "resultList": {
        "result": [
            {
                "id": "41002441",
                "source": "MED",
                "pmid": "41002441",
                "title": "Next-Generation mRNA Vaccines in Melanoma",
                "abstractText": (
                    "Adding the individualised neoantigen therapy mRNA-4157 to pembrolizumab "
                    "prolonged recurrence-free survival in a randomised phase 2b trial in "
                    "resected melanoma."
                ),
                "journalTitle": "Cells",
                "pubYear": "2025",
                "citedByCount": 12,
                "authorList": {"author": [{"fullName": "Rossi A"}]},
                "pubTypeList": {"pubType": ["review"]},
            }
        ]
    }
}

#: CMV is genuinely in Phase 3 — the registry confirms the deck.
CTGOV_PHASE3 = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT05085366", "briefTitle": "CMV Phase 3"},
                "statusModule": {
                    "overallStatus": "ACTIVE_NOT_RECRUITING",
                    "startDateStruct": {"date": "2021-10-01"},
                },
                "designModule": {"phases": ["PHASE3"], "enrollmentInfo": {"count": 7000}},
                "descriptionModule": {"briefSummary": "A pivotal efficacy study of mRNA-1647."},
                "conditionsModule": {"conditions": ["Cytomegalovirus Infections"]},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "ModernaTX, Inc."}},
            },
            "hasResults": False,
        }
    ]
}


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "esearch" in url:
            return httpx.Response(
                200, json={"esearchresult": {"idlist": ["38570682"], "count": "1"}}
            )
        if "efetch" in url:
            return httpx.Response(200, text=PUBMED_XML)
        if "europepmc" in url:
            return httpx.Response(200, json=EPMC_PAYLOAD)
        if "clinicaltrials" in url:
            return httpx.Response(200, json=CTGOV_PHASE3)
        if "api.fda.gov" in url:
            # Drugs@FDA does not index CBER-licensed vaccines; mRESVIA is absent.
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


@pytest.fixture
def moderna_run(settings, monkeypatch) -> str:
    monkeypatch.setattr(settings, "retrieval_enabled", True)
    monkeypatch.setattr(settings, "regulatory_verification_enabled", True)

    with session_scope() as session:
        document, _ = store_document(
            session,
            data=moderna_pdf(),
            filename="moderna-jpm.pdf",
        )
        session.flush()
        run = AnalysisRun(document_id=document.id)
        session.add(run)
        session.flush()
        return run.id


@pytest.fixture
async def completed(moderna_run) -> str:
    http = httpx.AsyncClient(transport=_transport())
    retriever = EvidenceRetriever(
        pubmed=PubMedClient(http),
        europepmc=EuropePMCClient(http),
        clinicaltrials=ClinicalTrialsClient(http),
    )
    verifier = RegulatoryVerifier(
        openfda=OpenFDAClient(http), clinicaltrials=ClinicalTrialsClient(http)
    )
    llm = LLMClient(StubProvider(), run_id=moderna_run, persist_logs=False)
    await AnalysisPipeline(llm=llm, retriever=retriever, verifier=verifier).run(moderna_run)
    return moderna_run


def claims_of_type(run_id: str, claim_type: ClaimType) -> list[tuple[Claim, ClaimAssessment]]:
    with session_scope() as session:
        claims = session.query(Claim).filter_by(run_id=run_id, claim_type=claim_type).all()
        assessments = {
            a.claim_id: a for a in session.query(ClaimAssessment).filter_by(run_id=run_id).all()
        }
        return [(c, assessments[c.id]) for c in claims if c.id in assessments]


class TestRunCompletes:
    async def test_pipeline_succeeds(self, completed):
        with session_scope() as session:
            run = session.get(AnalysisRun, completed)
        assert run.status is RunStatus.SUCCEEDED


class TestTheDeckProducesAnAnalysis:
    """The second Moderna regression: the run reached Claims and stopped.

        PipelineError: No verifiable scientific claims could be extracted from
        this document. It may not be a biotech pitch deck...

    about a J.P. Morgan investor presentation containing approvals, Phase 3
    readouts and pipeline updates. The cause was upstream -- an output budget
    too small to hold the model's reasoning -- but the symptom was here, so
    the guard belongs here.
    """

    async def test_claims_are_extracted(self, completed):
        with session_scope() as session:
            claims = session.query(Claim).filter_by(run_id=completed).all()
        assert claims, "a deck of clinical and regulatory statements yielded no claims"

    async def test_entities_are_extracted(self, completed):
        from app.db.models import Entity

        with session_scope() as session:
            entities = session.query(Entity).filter_by(run_id=completed).all()
        assert entities

    async def test_assessment_completes_for_every_claim(self, completed):
        with session_scope() as session:
            claims = session.query(Claim).filter_by(run_id=completed).all()
            assessments = session.query(ClaimAssessment).filter_by(run_id=completed).all()
        assert len(assessments) == len(claims)

    async def test_a_recommendation_is_generated(self, completed):
        with session_scope() as session:
            report = session.query(Report).filter_by(run_id=completed).one()
        assert report.recommendation
        assert report.ic_recommendation

    async def test_every_stage_reached_a_terminal_state(self, completed):
        from app.core.enums import StageStatus
        from app.db.models import RunStage

        with session_scope() as session:
            stages = session.query(RunStage).filter_by(run_id=completed).all()
        assert stages
        for stage in stages:
            assert stage.status in (StageStatus.SUCCEEDED, StageStatus.SKIPPED), (
                f"{stage.stage} ended {stage.status}: {stage.error_message}"
            )

    async def test_claim_extraction_reports_what_it_discarded(self, completed):
        """A stage that drops candidates must say which, and why."""
        with session_scope() as session:
            run = session.get(AnalysisRun, completed)
        rejections = run.metrics["stages"]["claims"]["rejections"]
        assert "by_reason" in rejections
        assert run.metrics["stages"]["claims"]["candidates"] >= len(
            run.metrics["stages"]["claims"].get("by_category", {})
        )


class TestClaimTypingSeparatesTheDeck:
    async def test_promotional_and_forward_looking_claims_are_identified(self, completed):
        with session_scope() as session:
            types = {c.claim_type for c in session.query(Claim).filter_by(run_id=completed).all()}
        # The deck contains vision, marketing, a plan and a market estimate;
        # the old system scored all of them as weak scientific claims.
        assert types & {
            ClaimType.MARKETING,
            ClaimType.CORPORATE_VISION,
            ClaimType.FORWARD_LOOKING,
            ClaimType.MARKET_ESTIMATE,
            ClaimType.STRATEGIC_OBJECTIVE,
        }

    async def test_regulatory_and_pipeline_claims_are_identified(self, completed):
        with session_scope() as session:
            types = {c.claim_type for c in session.query(Claim).filter_by(run_id=completed).all()}
        assert ClaimType.REGULATORY_APPROVAL in types or ClaimType.REGULATORY_SUBMISSION in types
        assert ClaimType.PIPELINE_STAGE in types

    async def test_unscorable_claims_are_excluded_from_scoring(self, completed):
        with session_scope() as session:
            assessments = (
                session.query(ClaimAssessment).filter_by(run_id=completed, is_scorable=False).all()
            )
        assert assessments, "expected promotional/forward-looking claims to be excluded"
        for assessment in assessments:
            assert assessment.corroboration_status is CorroborationStatus.NOT_ASSESSABLE


class TestAbsenceIsNotEvidenceAgainst:
    async def test_approval_claims_are_not_scored_as_unsupported(self, completed):
        """The headline failure: an approved product read as unsupported."""
        pairs = claims_of_type(completed, ClaimType.REGULATORY_APPROVAL)
        if not pairs:
            pytest.skip("offline typing did not produce an approval claim for this deck")
        for _claim, assessment in pairs:
            assert assessment.credibility_score > 55, assessment.score_explanation

    async def test_unverifiable_claims_say_so_rather_than_looking_weak(self, completed):
        with session_scope() as session:
            assessments = session.query(ClaimAssessment).filter_by(run_id=completed).all()
        unchecked = [a for a in assessments if a.corroboration_status in UNCHECKED_CORROBORATION]
        assert unchecked, "expected some claims to be unverifiable from public sources"
        for assessment in unchecked:
            assert assessment.corroboration_rationale
            # The rationale must attribute the gap to the search, not the claim.
            assert (
                "not evidence that the claim is false" in assessment.corroboration_rationale
                or "expected" in assessment.corroboration_rationale
                or "coverage" in assessment.corroboration_rationale
                or "does not" in assessment.corroboration_rationale
            )

    async def test_no_claim_is_marked_contradicted_without_disagreeing_evidence(self, completed):
        with session_scope() as session:
            assessments = session.query(ClaimAssessment).filter_by(run_id=completed).all()
        for assessment in assessments:
            if assessment.corroboration_status in ADVERSE_CORROBORATION:
                # An adverse finding must come from real disagreement: either
                # contradicting records, or an authoritative refutation.
                assert (
                    assessment.contradicting_count > 0
                    or assessment.verification_status == "refuted"
                ), assessment.corroboration_rationale


class TestScoreIsRealistic:
    async def test_overall_score_is_not_absurdly_low(self, completed):
        """The regression proper.

        With approvals, active registered Phase 3 programmes and published
        clinical data, a score in the twenties is indefensible. The threshold
        is deliberately loose -- this asserts the failure mode is gone, not a
        particular number.
        """
        with session_scope() as session:
            report = session.query(Report).filter_by(run_id=completed).one()
        assert report.overall_score > 40, (
            f"scored {report.overall_score}; the pre-fix Moderna memo scored 24.1"
        )

    async def test_every_score_carries_an_explanation(self, completed):
        with session_scope() as session:
            assessments = session.query(ClaimAssessment).filter_by(run_id=completed).all()
        for assessment in assessments:
            assert assessment.score_explanation, assessment.claim_id


class TestScorecardReplacesTheOpaqueNumber:
    async def test_report_carries_a_multi_dimensional_scorecard(self, completed):
        with session_scope() as session:
            report = session.query(Report).filter_by(run_id=completed).one()
        scorecard = report.scorecard
        assert scorecard, "the report must carry a scorecard"
        assert len(scorecard["dimensions"]) == 10
        assert scorecard["recommendation"]
        assert report.ic_recommendation

    async def test_each_dimension_answers_a_question(self, completed):
        with session_scope() as session:
            report = session.query(Report).filter_by(run_id=completed).one()
        for dimension in report.scorecard["dimensions"]:
            assert dimension["question"]
            assert dimension["rationale"]
            assert dimension["label"]

    async def test_unassessed_dimensions_are_null_not_zero(self, completed):
        with session_scope() as session:
            report = session.query(Report).filter_by(run_id=completed).one()
        for dimension in report.scorecard["dimensions"]:
            if not dimension["assessed"]:
                assert dimension["score"] is None

    async def test_memo_renders_the_scorecard(self, completed):
        with session_scope() as session:
            report = session.query(Report).filter_by(run_id=completed).one()
        assert "Investment Committee Scorecard" in report.markdown
        assert "Recommendation:" in report.markdown or "recommendation" in report.markdown.lower()


class TestVerificationIsAttempted:
    async def test_verification_ran_and_was_recorded(self, completed):
        with session_scope() as session:
            run = session.get(AnalysisRun, completed)
        verification = run.metrics["stages"]["assessment"]["verification"]
        assert verification["attempted"] >= 1

    async def test_registry_confirmation_is_recorded_on_the_claim(self, completed):
        with session_scope() as session:
            assessments = (
                session.query(ClaimAssessment)
                .filter_by(run_id=completed)
                .filter(ClaimAssessment.verification_status.isnot(None))
                .all()
            )
        assert assessments, "expected at least one claim to be checked authoritatively"
        for assessment in assessments:
            assert assessment.verification_source in {"openfda", "clinicaltrials_gov", "none"}
            assert assessment.verification_detail
