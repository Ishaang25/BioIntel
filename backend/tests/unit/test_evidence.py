"""Evidence source parsing, deduplication and ranking."""

from __future__ import annotations

import httpx
import pytest

from app.core.enums import EvidenceSource, PublicationType
from app.evidence.clinicaltrials import ClinicalTrialsClient
from app.evidence.europepmc import EuropePMCClient
from app.evidence.models import EvidenceRecord
from app.evidence.pubmed import PubMedClient, parse_pubmed_xml
from app.evidence.retriever import (
    ScoredRecord,
    cosine,
    deduplicate,
    diversify,
    lexical_relevance,
    quality_score,
)

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>35675433</PMID>
      <Article>
        <Journal>
          <Title>Science Translational Medicine</Title>
          <JournalIssue><PubDate><Year>2022</Year><Month>Jun</Month><Day>8</Day></PubDate></JournalIssue>
        </Journal>
        <ArticleTitle>Preclinical and clinical evaluation of the LRRK2 inhibitor DNL201</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">LRRK2 mutations are linked to Parkinson disease.</AbstractText>
          <AbstractText Label="RESULTS">DNL201 inhibited LRRK2 kinase activity in humans with acceptable tolerability.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author><LastName>Jennings</LastName><Initials>D</Initials></Author>
          <Author><CollectiveName>The LRRK2 Consortium</CollectiveName></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType>Clinical Trial, Phase I</PublicationType>
          <PublicationType>Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName>Parkinson Disease</DescriptorName></MeshHeading>
      </MeshHeadingList>
      <KeywordList><Keyword>LRRK2</Keyword></KeywordList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1126/scitranslmed.abj2658</ArticleId>
        <ArticleId IdType="pmc">PMC9999999</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>11111111</PMID>
      <Article>
        <ArticleTitle>A retracted study</ArticleTitle>
        <PublicationTypeList><PublicationType>Retracted Publication</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


class TestPubMedParsing:
    def test_parses_all_articles(self):
        assert len(parse_pubmed_xml(PUBMED_XML)) == 2

    def test_extracts_core_fields(self):
        record = parse_pubmed_xml(PUBMED_XML)[0]
        assert record.pmid == "35675433"
        assert record.doi == "10.1126/scitranslmed.abj2658"
        assert record.pmcid == "PMC9999999"
        assert record.publication_year == 2022
        assert record.journal == "Science Translational Medicine"
        assert record.mesh_terms == ["Parkinson Disease"]
        assert record.url == "https://pubmed.ncbi.nlm.nih.gov/35675433/"

    def test_structured_abstract_keeps_section_labels(self):
        abstract = parse_pubmed_xml(PUBMED_XML)[0].abstract
        assert abstract.startswith("Background:")
        assert "Results:" in abstract

    def test_collective_authors_are_kept(self):
        authors = parse_pubmed_xml(PUBMED_XML)[0].authors
        assert authors == ["Jennings D", "The LRRK2 Consortium"]

    def test_study_design_from_publication_type(self):
        assert parse_pubmed_xml(PUBMED_XML)[0].study_design is PublicationType.CLINICAL_TRIAL

    def test_retraction_is_detected(self):
        retracted = parse_pubmed_xml(PUBMED_XML)[1]
        assert retracted.is_retracted is True
        assert retracted.study_design is PublicationType.RETRACTED
        assert quality_score(retracted) == 0.0

    def test_malformed_xml_returns_empty(self):
        assert parse_pubmed_xml("<not-xml") == []
        assert parse_pubmed_xml("") == []


class TestStudyDesignInference:
    def test_preclinical_inferred_from_abstract(self):
        record = EvidenceRecord(
            source=EvidenceSource.PUBMED,
            external_id="1",
            title="Effect in mice",
            abstract="Mice were treated in vitro and in vivo; tumour volume decreased.",
        )
        assert record.study_design is PublicationType.PRECLINICAL

    def test_clinical_cues_beat_preclinical_absence(self):
        record = EvidenceRecord(
            source=EvidenceSource.PUBMED,
            external_id="2",
            title="Cohort",
            abstract="Patients were enrolled and followed for two years.",
        )
        assert record.study_design is PublicationType.OBSERVATIONAL

    def test_registry_records_are_registry_design(self):
        record = EvidenceRecord(source=EvidenceSource.CLINICALTRIALS_GOV, external_id="NCT1")
        assert record.study_design is PublicationType.REGISTRY_RECORD


class TestDeduplication:
    def test_same_doi_collapses_across_sources(self):
        a = EvidenceRecord(
            source=EvidenceSource.PUBMED,
            external_id="1",
            doi="10.1/x",
            title="T",
            abstract="a" * 200,
        )
        b = EvidenceRecord(
            source=EvidenceSource.EUROPE_PMC,
            external_id="MED:1",
            doi="10.1/X",
            title="T",
            citation_count=42,
        )
        merged = deduplicate([a, b])
        assert len(merged) == 1
        assert merged[0].source is EvidenceSource.PUBMED
        assert merged[0].citation_count == 42  # merged from Europe PMC

    def test_missing_abstract_is_backfilled(self):
        a = EvidenceRecord(source=EvidenceSource.PUBMED, external_id="1", pmid="1", abstract="")
        b = EvidenceRecord(
            source=EvidenceSource.EUROPE_PMC,
            external_id="MED:1",
            pmid="1",
            abstract="the real abstract",
        )
        assert deduplicate([a, b])[0].abstract == "the real abstract"

    def test_distinct_records_survive(self):
        records = [
            EvidenceRecord(source=EvidenceSource.PUBMED, external_id=str(i), pmid=str(i))
            for i in range(4)
        ]
        assert len(deduplicate(records)) == 4


class TestQualityScoring:
    def _record(self, **kwargs) -> EvidenceRecord:
        base = {
            "source": EvidenceSource.PUBMED,
            "external_id": "1",
            "title": "T",
            "abstract": "a" * 400,
            "publication_year": 2023,
        }
        return EvidenceRecord(**{**base, **kwargs})

    def test_meta_analysis_beats_case_report(self):
        meta = self._record(publication_types=["Meta-Analysis"])
        case = self._record(publication_types=["Case Reports"])
        assert quality_score(meta) > quality_score(case)

    def test_recent_beats_old(self):
        assert quality_score(self._record(publication_year=2024)) > quality_score(
            self._record(publication_year=1985)
        )

    def test_missing_abstract_lowers_score(self):
        assert quality_score(self._record(abstract="")) < quality_score(self._record())

    def test_preprint_is_penalised(self):
        assert quality_score(self._record(is_preprint=True)) < quality_score(self._record())

    def test_score_is_bounded(self):
        score = quality_score(
            self._record(publication_types=["Meta-Analysis"], citation_count=100_000)
        )
        assert 0.0 <= score <= 1.0


class TestRelevance:
    def test_lexical_relevance_prefers_on_topic(self):
        claim = "NG-101 inhibits LRRK2 kinase in Parkinson disease"
        on_topic = EvidenceRecord(
            source=EvidenceSource.PUBMED,
            external_id="1",
            title="LRRK2 kinase inhibition in Parkinson disease",
            abstract="LRRK2 kinase inhibitors reduce neurodegeneration.",
        )
        off_topic = EvidenceRecord(
            source=EvidenceSource.PUBMED,
            external_id="2",
            title="Retail supply chain optimisation",
            abstract="Warehouse logistics were modelled.",
        )
        assert lexical_relevance(claim, on_topic) > lexical_relevance(claim, off_topic)

    def test_cosine_handles_degenerate_input(self):
        assert cosine([], []) == 0.0
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


class TestDiversify:
    def test_reserves_a_slot_per_source(self):
        scored = [
            ScoredRecord(
                record=EvidenceRecord(
                    source=EvidenceSource.PUBMED, external_id=str(i), title=f"Paper {i}"
                ),
                relevance=0.9,
                quality=0.9,
                rank_score=0.9 - i * 0.01,
            )
            for i in range(6)
        ]
        scored.append(
            ScoredRecord(
                record=EvidenceRecord(
                    source=EvidenceSource.CLINICALTRIALS_GOV, external_id="NCT1", title="A trial"
                ),
                relevance=0.2,
                quality=0.2,
                rank_score=0.05,
            )
        )
        selected = diversify(scored, limit=3)
        assert {s.record.source for s in selected} == {
            EvidenceSource.PUBMED,
            EvidenceSource.CLINICALTRIALS_GOV,
        }

    def test_returns_everything_when_under_limit(self):
        scored = [
            ScoredRecord(
                record=EvidenceRecord(source=EvidenceSource.PUBMED, external_id="1"),
                relevance=1.0,
                quality=1.0,
                rank_score=1.0,
            )
        ]
        assert diversify(scored, limit=8) == scored


class TestClientsAgainstMockedHttp:
    async def test_pubmed_search_round_trip(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "esearch" in str(request.url):
                return httpx.Response(
                    200, json={"esearchresult": {"idlist": ["35675433"], "count": "1"}}
                )
            return httpx.Response(200, text=PUBMED_XML)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = PubMedClient(http)
            records = await client.search("LRRK2[tiab]", limit=1)
        assert len(records) == 2
        assert records[0].retrieval_query == "LRRK2[tiab]"

    async def test_europepmc_parsing(self):
        payload = {
            "resultList": {
                "result": [
                    {
                        "id": "35675433",
                        "source": "MED",
                        "pmid": "35675433",
                        "doi": "10.1/x",
                        "title": "LRRK2 study",
                        "abstractText": "We studied LRRK2.",
                        "journalTitle": "J Neuro",
                        "pubYear": "2022",
                        "citedByCount": 12,
                        "authorList": {"author": [{"fullName": "Jennings D"}]},
                        "pubTypeList": {"pubType": ["research-article"]},
                    }
                ]
            }
        }
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as http:
            records = await EuropePMCClient(http).search("LRRK2")
        assert records[0].citation_count == 12
        assert records[0].pmid == "35675433"
        assert records[0].source is EvidenceSource.EUROPE_PMC

    async def test_clinicaltrials_parsing_flags_terminated(self):
        payload = {
            "studies": [
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": "NCT01234567", "briefTitle": "A study"},
                        "statusModule": {
                            "overallStatus": "TERMINATED",
                            "whyStopped": "Futility at interim analysis",
                            "startDateStruct": {"date": "2019-04-01"},
                        },
                        "designModule": {
                            "phases": ["PHASE2"],
                            "enrollmentInfo": {"count": 120, "type": "ACTUAL"},
                        },
                        "descriptionModule": {"briefSummary": "Testing an LRRK2 inhibitor."},
                        "conditionsModule": {"conditions": ["Parkinson Disease"]},
                        "outcomesModule": {"primaryOutcomes": [{"measure": "UPDRS III"}]},
                        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Bio"}},
                    },
                    "hasResults": False,
                }
            ]
        }
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as http:
            records = await ClinicalTrialsClient(http).search(conditions=["Parkinson Disease"])
        trial = records[0].trial
        assert records[0].nct_id == "NCT01234567"
        assert trial["is_negative_signal"] is True
        assert trial["why_stopped"] == "Futility at interim analysis"
        assert trial["enrollment"] == 120
        assert trial["primary_outcomes"] == ["UPDRS III"]

    async def test_no_query_terms_returns_nothing_without_calling(self):
        def handler(request):  # pragma: no cover - must not be reached
            raise AssertionError("should not issue a request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            assert await ClinicalTrialsClient(http).search() == []


class TestCitations:
    def test_citation_label_prefers_pmid(self):
        record = EvidenceRecord(
            source=EvidenceSource.PUBMED, external_id="1", pmid="123", doi="10.1/x"
        )
        assert record.citation_label == "PMID:123"

    def test_short_citation_uses_surname(self):
        record = EvidenceRecord(
            source=EvidenceSource.PUBMED,
            external_id="1",
            pmid="123",
            authors=["Jennings D"],
            journal="Nature",
            publication_year=2022,
        )
        assert record.short_citation() == "Jennings et al., Nature (2022); PMID:123"

    def test_short_citation_without_authors(self):
        record = EvidenceRecord(source=EvidenceSource.PUBMED, external_id="1", pmid="123")
        assert "Unknown" in record.short_citation()
