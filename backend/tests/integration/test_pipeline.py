"""End-to-end pipeline tests.

These drive the real orchestrator over a real (synthetic) PDF, with the
deterministic LLM provider and mocked literature sources.  They assert on
behaviour that matters to a user: that claims trace to the document, that
contradicting evidence lands, that the report is produced, and that a failing
non-critical stage degrades rather than aborts.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.enums import PipelineStage, RunStatus, StageStatus
from app.db.models import (
    AnalysisRun,
    Claim,
    ClaimAssessment,
    ClaimEvidenceLink,
    DiligenceQuestion,
    Document,
    DocumentPage,
    Entity,
    EvidenceItem,
    PageBlock,
    Report,
    RiskFlag,
    RunStage,
)
from app.db.session import session_scope
from app.evidence.clinicaltrials import ClinicalTrialsClient
from app.evidence.europepmc import EuropePMCClient
from app.evidence.pubmed import PubMedClient
from app.evidence.retriever import EvidenceRetriever
from app.llm.client import LLMClient
from app.llm.stub_provider import StubProvider
from app.pipeline.orchestrator import AnalysisPipeline
from app.services.documents import store_document
from tests.fixtures.sample_deck import NEUROGEN_PAGES, neurogen_pdf, tiny_pdf

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>35675433</PMID>
      <Article>
        <Journal>
          <Title>Science Translational Medicine</Title>
          <JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue>
        </Journal>
        <ArticleTitle>Clinical evaluation of an LRRK2 inhibitor in Parkinson disease</ArticleTitle>
        <Abstract><AbstractText>LRRK2 kinase inhibition reduced dopaminergic neuron loss in
        preclinical models of Parkinson disease. In humans the compound was well tolerated and
        engaged its target, but did not significantly improve motor outcomes over 12 weeks.</AbstractText></Abstract>
        <AuthorList><Author><LastName>Jennings</LastName><Initials>D</Initials></Author></AuthorList>
        <PublicationTypeList><PublicationType>Clinical Trial, Phase I</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1126/x</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""

EPMC_PAYLOAD = {
    "resultList": {
        "result": [
            {
                "id": "40000001",
                "source": "MED",
                "pmid": "40000001",
                "doi": "10.1000/epmc1",
                "title": "MPTP mouse models of Parkinson disease: translational limitations",
                "abstractText": (
                    "The MPTP mouse model reproduces acute dopaminergic loss but does not "
                    "recapitulate alpha-synuclein pathology. Effects observed in this model "
                    "have repeatedly failed to translate to human disease modification."
                ),
                "journalTitle": "Neurobiology of Disease",
                "pubYear": "2021",
                "citedByCount": 87,
                "authorList": {"author": [{"fullName": "Alvarez S"}]},
                "pubTypeList": {"pubType": ["review"]},
            }
        ]
    }
}

CTGOV_PAYLOAD = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT03710707",
                    "briefTitle": "LRRK2 inhibitor Phase 1b",
                },
                "statusModule": {
                    "overallStatus": "TERMINATED",
                    "whyStopped": "Sponsor decision following non-clinical findings",
                    "startDateStruct": {"date": "2020-02-01"},
                },
                "designModule": {
                    "phases": ["PHASE1"],
                    "enrollmentInfo": {"count": 48, "type": "ACTUAL"},
                },
                "descriptionModule": {
                    "briefSummary": "A study of an LRRK2 inhibitor in Parkinson disease."
                },
                "conditionsModule": {"conditions": ["Parkinson Disease"]},
                "outcomesModule": {"primaryOutcomes": [{"measure": "Safety and tolerability"}]},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Denali"}},
            },
            "hasResults": False,
        }
    ]
}


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "esearch" in url:
            return httpx.Response(
                200, json={"esearchresult": {"idlist": ["35675433"], "count": "1"}}
            )
        if "efetch" in url:
            return httpx.Response(200, text=PUBMED_XML)
        if "europepmc" in url:
            return httpx.Response(200, json=EPMC_PAYLOAD)
        if "clinicaltrials" in url:
            return httpx.Response(200, json=CTGOV_PAYLOAD)
        return httpx.Response(404, json={})  # pragma: no cover

    return httpx.MockTransport(handler)


@pytest.fixture
def retriever() -> EvidenceRetriever:
    http = httpx.AsyncClient(transport=_mock_transport())
    return EvidenceRetriever(
        pubmed=PubMedClient(http),
        europepmc=EuropePMCClient(http),
        clinicaltrials=ClinicalTrialsClient(http),
    )


@pytest.fixture
def run_id() -> str:
    with session_scope() as session:
        document, _ = store_document(session, data=neurogen_pdf(), filename="neurogen.pdf")
        session.flush()
        run = AnalysisRun(document_id=document.id)
        session.add(run)
        session.flush()
        return run.id


@pytest.fixture
async def completed_run(run_id, retriever, settings, monkeypatch) -> str:
    monkeypatch.setattr(settings, "retrieval_enabled", True)
    llm = LLMClient(StubProvider(), run_id=run_id, persist_logs=False)
    await AnalysisPipeline(llm=llm, retriever=retriever).run(run_id)
    return run_id


class TestFullRun:
    async def test_run_reaches_succeeded(self, completed_run):
        with session_scope() as session:
            run = session.get(AnalysisRun, completed_run)
            assert run.status is RunStatus.SUCCEEDED
            assert run.progress == 1.0
            assert run.error_code is None
            assert run.duration_ms is not None

    async def test_every_stage_ran(self, completed_run):
        with session_scope() as session:
            stages = session.query(RunStage).filter_by(run_id=completed_run).all()
        assert len(stages) == 10
        assert all(s.status is StageStatus.SUCCEEDED for s in stages), [
            (s.stage, s.status, s.error_message)
            for s in stages
            if s.status is not StageStatus.SUCCEEDED
        ]

    async def test_pages_and_blocks_are_persisted(self, completed_run):
        with session_scope() as session:
            pages = session.query(DocumentPage).all()
            blocks = session.query(PageBlock).all()
        assert len(pages) == len(NEUROGEN_PAGES)
        assert blocks
        assert all(p.char_count > 0 for p in pages)

    async def test_claims_are_extracted_with_verified_provenance(self, completed_run):
        with session_scope() as session:
            claims = session.query(Claim).filter_by(run_id=completed_run).all()
            pages = {p.page_number: p.text for p in session.query(DocumentPage).all()}

        assert len(claims) >= 8
        for claim in claims:
            assert claim.verbatim_quote, "every claim must carry a quote"
            assert claim.page_number in pages
            assert claim.quote_verification in ("exact", "fuzzy")

    async def test_claim_quotes_actually_occur_in_the_document(self, completed_run):
        """The core anti-hallucination guarantee, verified independently."""
        from app.core.enums import QuoteVerification
        from app.utils.text import verify_quote

        with session_scope() as session:
            claims = session.query(Claim).filter_by(run_id=completed_run).all()
            corpus = "\n".join(p.text for p in session.query(DocumentPage).all())

        for claim in claims:
            verification, _ = verify_quote(claim.verbatim_quote, corpus)
            assert verification is not QuoteVerification.NOT_FOUND, claim.verbatim_quote[:80]

    async def test_entities_are_extracted(self, completed_run):
        with session_scope() as session:
            entities = session.query(Entity).filter_by(run_id=completed_run).all()
        names = {(e.canonical_name or e.name).lower() for e in entities}
        assert any("lrrk2" in n for n in names)
        assert any("parkinson" in n for n in names)

    async def test_evidence_is_retrieved_and_linked(self, completed_run):
        with session_scope() as session:
            evidence = session.query(EvidenceItem).all()
            links = session.query(ClaimEvidenceLink).filter_by(run_id=completed_run).all()
        assert evidence
        assert links
        assert {e.source for e in evidence} >= {"pubmed", "europe_pmc"}

    async def test_terminated_trial_is_captured(self, completed_run):
        with session_scope() as session:
            trial = session.query(EvidenceItem).filter_by(nct_id="NCT03710707").one_or_none()
        assert trial is not None
        assert trial.trial["is_negative_signal"] is True

    async def test_every_claim_is_assessed(self, completed_run):
        with session_scope() as session:
            claims = session.query(Claim).filter_by(run_id=completed_run).all()
            assessments = session.query(ClaimAssessment).filter_by(run_id=completed_run).all()
        assert len(assessments) == len(claims)
        assert all(0 <= a.credibility_score <= 100 for a in assessments)
        assert all(0 <= a.confidence <= 1 for a in assessments)

    async def test_risks_and_questions_are_generated(self, completed_run):
        with session_scope() as session:
            risks = session.query(RiskFlag).filter_by(run_id=completed_run).all()
            questions = session.query(DiligenceQuestion).filter_by(run_id=completed_run).all()
        assert risks
        assert questions
        assert any(r.is_rule_based for r in risks), "deterministic rules must always contribute"
        assert all(q.question.strip().endswith("?") for q in questions)

    async def test_deterministic_rules_catch_the_seeded_flaws(self, completed_run):
        """The sample deck contains known defects; they must be surfaced."""
        with session_scope() as session:
            risks = session.query(RiskFlag).filter_by(run_id=completed_run).all()
        titles = " ".join(r.title.lower() for r in risks)
        assert "sample size" in titles or "comparator" in titles or "statistics" in titles

    async def test_report_is_produced(self, completed_run):
        with session_scope() as session:
            report = session.query(Report).filter_by(run_id=completed_run).one()
        assert report.executive_summary
        assert report.sections
        assert report.markdown.startswith("# ")
        assert 0 <= report.overall_score <= 100
        assert report.limitations

    async def test_report_markdown_flags_degraded_mode(self, completed_run):
        with session_scope() as session:
            report = session.query(Report).filter_by(run_id=completed_run).one()
        assert "Degraded run" in report.markdown

    async def test_metrics_are_recorded_for_every_stage(self, completed_run):
        with session_scope() as session:
            run = session.get(AnalysisRun, completed_run)
        assert set(run.metrics["stages"]) == {s.value for s in PipelineStage}
        assert run.metrics["counts"]["claims"] > 0
        assert "llm" in run.metrics

    async def test_config_snapshot_supports_reproducibility(self, completed_run):
        with session_scope() as session:
            run = session.get(AnalysisRun, completed_run)
        assert run.config["prompt_version"]
        assert run.config["pipeline_version"]
        assert run.config["models"]["reasoning"]


class TestFailureBehaviour:
    async def test_document_without_claims_fails_the_run(self, retriever):
        with session_scope() as session:
            document, _ = store_document(session, data=tiny_pdf(), filename="tiny.pdf")
            session.flush()
            run = AnalysisRun(document_id=document.id)
            session.add(run)
            session.flush()
            run_id = run.id

        from app.core.errors import PipelineError

        llm = LLMClient(StubProvider(), persist_logs=False)
        with pytest.raises(PipelineError, match="claims"):
            await AnalysisPipeline(llm=llm, retriever=retriever).run(run_id)

        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
        assert run.status is RunStatus.FAILED
        assert run.error_message

    async def test_retrieval_failure_degrades_rather_than_aborts(
        self, run_id, settings, monkeypatch
    ):
        """A PubMed outage must not cost the analyst the whole report."""
        monkeypatch.setattr(settings, "retrieval_enabled", True)

        def exploding(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "service unavailable"})

        monkeypatch.setattr(settings, "retrieval_max_retries", 1)
        http = httpx.AsyncClient(transport=httpx.MockTransport(exploding))
        retriever = EvidenceRetriever(
            pubmed=PubMedClient(http),
            europepmc=EuropePMCClient(http),
            clinicaltrials=ClinicalTrialsClient(http),
        )
        llm = LLMClient(StubProvider(), persist_logs=False)
        await AnalysisPipeline(llm=llm, retriever=retriever).run(run_id)

        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
            report = session.query(Report).filter_by(run_id=run_id).one_or_none()
        assert run.status is RunStatus.SUCCEEDED
        assert report is not None
        assert any("literature" in w.lower() for w in run.metrics["warnings"])

    async def test_cancellation_stops_the_run(self, run_id, retriever):
        llm = LLMClient(StubProvider(), persist_logs=False)
        from app.pipeline.orchestrator import CancelledError

        calls = {"n": 0}

        def should_cancel() -> bool:
            calls["n"] += 1
            return calls["n"] > 2

        with pytest.raises(CancelledError):
            await AnalysisPipeline(llm=llm, retriever=retriever, should_cancel=should_cancel).run(
                run_id
            )

        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
        assert run.status is RunStatus.CANCELLED

    async def test_missing_source_file_fails_cleanly(self, run_id, retriever):
        from pathlib import Path

        from app.core.errors import PipelineError

        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
            document = session.get(Document, run.document_id)
            Path(document.storage_path).unlink()

        llm = LLMClient(StubProvider(), persist_logs=False)
        with pytest.raises(PipelineError, match="missing"):
            await AnalysisPipeline(llm=llm, retriever=retriever).run(run_id)


class TestApiAfterRun:
    async def test_artefacts_are_readable_over_the_api(self, completed_run, client):
        api = "/api/v1"
        detail = client.get(f"{api}/runs/{completed_run}").json()
        assert detail["status"] == "succeeded"
        assert detail["counts"]["claims"] > 0
        assert detail["degraded"] is True

        claims = client.get(f"{api}/runs/{completed_run}/claims").json()
        assert claims["total"] > 0
        first = claims["items"][0]
        assert first["assessment"]["credibility_band"]

        detail_claim = client.get(f"{api}/runs/{completed_run}/claims/{first['id']}").json()
        assert "evidence_links" in detail_claim

        assert client.get(f"{api}/runs/{completed_run}/entities").json()
        assert client.get(f"{api}/runs/{completed_run}/evidence").json()
        assert client.get(f"{api}/runs/{completed_run}/questions").json()
        assert client.get(f"{api}/runs/{completed_run}/risks").json()

        report = client.get(f"{api}/runs/{completed_run}/report").json()
        assert report["executive_summary"]

    async def test_report_exports(self, completed_run, client):
        api = "/api/v1"
        markdown = client.get(f"{api}/runs/{completed_run}/report/export")
        assert markdown.headers["content-type"].startswith("text/markdown")
        assert markdown.text.startswith("# ")

        html = client.get(f"{api}/runs/{completed_run}/report/export", params={"format": "html"})
        assert html.headers["content-type"].startswith("text/html")
        assert "<!doctype html>" in html.text.lower()
        assert "Executive Summary" in html.text

    async def test_invalid_export_format_is_rejected(self, completed_run, client):
        response = client.get(
            f"/api/v1/runs/{completed_run}/report/export", params={"format": "docx"}
        )
        assert response.status_code == 422

    async def test_claim_filters(self, completed_run, client):
        api = "/api/v1"
        critical = client.get(
            f"{api}/runs/{completed_run}/claims", params={"thesis_critical": True}
        ).json()
        assert all(c["is_thesis_critical"] for c in critical["items"])

    async def test_metrics_endpoint(self, completed_run, client):
        body = client.get(f"/api/v1/runs/{completed_run}/metrics").json()
        assert body["metrics"]["counts"]["claims"] > 0
        assert body["config"]["pipeline_version"]
