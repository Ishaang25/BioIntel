"""The benchmark suite: five companies, and the evidence each one meets.

Each deck is paired with a fixed set of external responses. The pairing is the
point -- a benchmark is a deck *and* the world it is checked against, and both
have to be pinned for the captured metrics to mean anything.
"""

from __future__ import annotations

import httpx

from tests.benchmarks.harness import BenchmarkDeck, evidence_transport
from tests.fixtures.beam_deck import beam_pdf
from tests.fixtures.biontech_deck import biontech_pdf
from tests.fixtures.crispr_deck import crispr_pdf
from tests.fixtures.moderna_deck import moderna_pdf
from tests.fixtures.recursion_deck import recursion_pdf


def _pubmed(pmid: str, title: str, abstract: str, year: str = "2023") -> str:
    return f"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>{pmid}</PMID>
      <Article>
        <Journal>
          <Title>New England Journal of Medicine</Title>
          <JournalIssue><PubDate><Year>{year}</Year></PubDate></JournalIssue>
        </Journal>
        <ArticleTitle>{title}</ArticleTitle>
        <Abstract><AbstractText>{abstract}</AbstractText></Abstract>
        <AuthorList><Author><LastName>Frangoul</LastName><Initials>H</Initials></Author></AuthorList>
        <PublicationTypeList><PublicationType>Clinical Trial, Phase 3</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1056/x</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


def _epmc(record_id: str, title: str, abstract: str, year: str = "2023") -> dict:
    return {
        "resultList": {
            "result": [
                {
                    "id": record_id,
                    "source": "MED",
                    "pmid": record_id,
                    "doi": f"10.1000/{record_id}",
                    "title": title,
                    "abstractText": abstract,
                    "journalTitle": "Nature Biotechnology",
                    "pubYear": year,
                    "citedByCount": 120,
                    "authorList": {"author": [{"fullName": "Reviewer A"}]},
                    "pubTypeList": {"pubType": ["review"]},
                }
            ]
        }
    }


def _ctgov(nct: str, title: str, phase: str, status: str, sponsor: str) -> dict:
    return {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {"nctId": nct, "briefTitle": title},
                    "statusModule": {
                        "overallStatus": status,
                        "startDateStruct": {"date": "2021-06-01"},
                    },
                    "designModule": {
                        "phases": [phase],
                        "enrollmentInfo": {"count": 45, "type": "ACTUAL"},
                    },
                    "descriptionModule": {"briefSummary": title},
                    "conditionsModule": {"conditions": ["Sickle Cell Disease"]},
                    "outcomesModule": {"primaryOutcomes": [{"measure": "Efficacy"}]},
                    "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor}},
                },
                "hasResults": True,
            }
        ]
    }


_MODERNA_PUBMED = _pubmed(
    "38570682",
    "Efficacy and safety of an mRNA-based RSV vaccine in older adults",
    "An mRNA-1345 RSV vaccine demonstrated efficacy against RSV-associated lower "
    "respiratory tract disease in adults 60 years of age and older.",
    "2024",
)

_TRANSPORTS = {
    "biontech": lambda: evidence_transport(
        pubmed_xml=_pubmed(
            "35675433",
            "An RNA vaccine platform in advanced solid tumours",
            "Individualised neoantigen mRNA therapy induced T-cell responses in "
            "patients with solid tumours. Objective response rates in early-phase "
            "studies remain modest and durability is not established.",
        ),
        epmc_payload=_epmc(
            "40000010",
            "Bispecific antibodies targeting PD-L1 and VEGF-A: state of the field",
            "PD-L1xVEGF-A bispecific antibodies have entered late-phase testing. "
            "Comparative benefit over checkpoint monotherapy is not yet established.",
        ),
        ctgov_payload=_ctgov(
            "NCT05012098", "BNT327 in NSCLC", "PHASE3", "RECRUITING", "BioNTech SE"
        ),
        pubmed_ids=["35675433"],
    ),
    "moderna": lambda: evidence_transport(
        pubmed_xml=_MODERNA_PUBMED,
        epmc_payload=_epmc(
            "40000020",
            "mRNA vaccine platforms: clinical translation and limitations",
            "mRNA platforms have delivered licensed vaccines. Claims of platform-wide "
            "probability of success are not independently established.",
        ),
        ctgov_payload=_ctgov(
            "NCT05127434", "mRNA-1647 CMV vaccine", "PHASE3", "ACTIVE_NOT_RECRUITING", "ModernaTX"
        ),
        pubmed_ids=["38570682"],
        # Drugs@FDA does not index CBER-licensed vaccines; mRESVIA is absent.
        openfda_payload=None,
    ),
    "crispr": lambda: evidence_transport(
        pubmed_xml=_pubmed(
            "34891123",
            "Exagamglogene autotemcel for severe sickle cell disease",
            "Exagamglogene autotemcel eliminated vaso-occlusive crises in the large "
            "majority of treated patients, with durable fetal hemoglobin induction.",
        ),
        epmc_payload=_epmc(
            "40000030",
            "Allogeneic CAR-T: persistence and rejection",
            "Allogeneic CAR-T products have shown responses in early-phase studies, "
            "but limited persistence relative to autologous products remains a "
            "consistent finding.",
        ),
        ctgov_payload=_ctgov(
            "NCT03745287", "CTX001 in sickle cell disease", "PHASE3", "COMPLETED", "Vertex"
        ),
        pubmed_ids=["34891123"],
    ),
    "recursion": lambda: evidence_transport(
        pubmed_xml=_pubmed(
            "37001234",
            "Machine learning for phenotypic drug discovery",
            "Image-based phenotypic screening with deep learning can recover known "
            "mechanism-of-action relationships. Prospective clinical validation of "
            "AI-derived targets remains limited.",
            "2023",
        ),
        epmc_payload=_epmc(
            "40000040",
            "AI in drug discovery: evidence for improved success rates",
            "Claims of reduced timelines and improved success rates from AI-driven "
            "discovery are not yet supported by controlled comparisons.",
        ),
        ctgov_payload=_ctgov(
            "NCT05085964",
            "REC-994 in cerebral cavernous malformation",
            "PHASE2",
            "COMPLETED",
            "Recursion Pharmaceuticals",
        ),
        pubmed_ids=["37001234"],
    ),
    "beam": lambda: evidence_transport(
        pubmed_xml=_pubmed(
            "36123456",
            "Base editing in hematopoietic stem cells",
            "Adenine base editing achieved high on-target conversion in "
            "hematopoietic stem cells without detectable double-stranded breaks. "
            "Long-term engraftment data in humans are not yet available.",
            "2022",
        ),
        epmc_payload=_epmc(
            "40000050",
            "Off-target assessment for base editors",
            "Off-target activity of base editors depends heavily on the assay used; "
            "internal assays frequently under-report relative to orthogonal methods.",
        ),
        ctgov_payload=_ctgov(
            "NCT05456880",
            "BEAM-101 in sickle cell disease",
            "PHASE1",
            "RECRUITING",
            "Beam Therapeutics",
        ),
        pubmed_ids=["36123456"],
    ),
}


DECKS: list[BenchmarkDeck] = [
    BenchmarkDeck(
        key="biontech",
        company="BioNTech",
        build=biontech_pdf,
        rationale=(
            "Breadth and size. 50 pages, 12 programmes, heavy entity repetition -- "
            "the deck that exposed the entity-extraction bottleneck and the "
            "truncated-output failure. Guards chunking, deduplication and runtime."
        ),
    ),
    BenchmarkDeck(
        key="moderna",
        company="Moderna",
        build=moderna_pdf,
        rationale=(
            "Approved products plus unverifiable track record. The deck that scored "
            "24.1/100 'unsupported' despite two FDA-approved products. Guards the "
            "separation of 'we could not check' from 'the evidence disagrees'."
        ),
    ),
    BenchmarkDeck(
        key="crispr",
        company="CRISPR Therapeutics",
        build=crispr_pdf,
        rationale=(
            "An approved therapy alongside a pre-clinical pipeline. Guards against "
            "a scorer that can only handle one epistemic regime at a time."
        ),
    ),
    BenchmarkDeck(
        key="recursion",
        company="Recursion Pharmaceuticals",
        build=recursion_pdf,
        rationale=(
            "Platform capability claims with thin clinical evidence. Guards the "
            "plausible_unverified path: 26 such claims, none contradicted."
        ),
    ),
    BenchmarkDeck(
        key="beam",
        company="Beam Therapeutics",
        build=beam_pdf,
        rationale=(
            "Proprietary preclinical data and track record. Guards the "
            "not_independently_verified path: 37 such claims, none contradicted."
        ),
    ),
]


def transport_for(key: str) -> httpx.MockTransport:
    return _TRANSPORTS[key]()
