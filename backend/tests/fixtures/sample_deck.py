"""Synthetic biotech pitch decks used across the test suite.

The content is deliberately realistic -- and deliberately flawed.  It contains
the failure patterns BioIntel exists to catch: an effect size with no control,
a clinical claim supported only by mouse data, marketing superlatives, hedged
assertions, and a number presented without statistics.  Tests assert that the
pipeline finds them.
"""

from __future__ import annotations

from app.ingestion.pdf_parser import build_pdf

NEUROGEN_PAGES: list[str] = [
    """NeuroGen Therapeutics

Disease-modifying therapy for Parkinson's disease

Series A — raising $28M
Confidential — for discussion purposes only""",
    """The problem

Parkinson's disease affects 10 million people worldwide.
Current therapies treat symptoms only; none slow neurodegeneration.
Levodopa loses efficacy after 5-7 years and causes dyskinesia.

There is no approved disease-modifying therapy for Parkinson's disease.""",
    """Our approach: allosteric LRRK2 inhibition

LRRK2 gain-of-function mutations are the most common genetic cause of
Parkinson's disease. Elevated LRRK2 kinase activity is also observed in
idiopathic patients.

NG-101 is a brain-penetrant allosteric LRRK2 kinase inhibitor with an IC50 of
3.2 nM in a biochemical assay.

Selectivity across a 468-kinase panel exceeds 100-fold.""",
    """Preclinical efficacy

In the MPTP mouse model, NG-101 reduced dopaminergic neuron loss by 62% versus
vehicle (p<0.01, n=24).

Striatal dopamine levels were preserved at 78% of baseline.

Motor function in the rotarod assay improved 2.4-fold.

We believe these data support disease modification in patients.""",
    """Safety and tolerability

NG-101 was well tolerated in 28-day rat and cynomolgus macaque toxicology
studies with no adverse findings at exposures 40-fold above the projected
human efficacious dose.

No pulmonary findings were observed at any dose.

Our compound has a best-in-class therapeutic index.""",
    """Biomarker strategy

Phosphorylated Rab10 in peripheral blood mononuclear cells is our target
engagement biomarker.

We will use CSF alpha-synuclein and neurofilament light chain as exploratory
progression biomarkers.

UPDRS Part III will be the primary clinical endpoint in our Phase 1b study.""",
    """Development plan

IND-enabling studies complete Q3.
Phase 1 single- and multiple-ascending-dose study in healthy volunteers begins Q1 next year.
Phase 1b in LRRK2-mutation carriers follows.

We expect proof of biological activity within 18 months.""",
    """Why we win

First-in-class allosteric mechanism avoids the ATP-binding site.
Revolutionary selectivity profile eliminates off-target liabilities.
Our platform is a breakthrough in neurodegeneration drug discovery.

Composition-of-matter patent filed; freedom to operate opinion obtained.""",
    """Team

Dr. Elena Marsh, PhD — CEO. Previously VP Discovery at a large pharma company.
Dr. Raj Patel, MD PhD — CMO. Led three CNS clinical programmes.
Dr. Sofia Alvarez, PhD — CSO. 40 publications in LRRK2 biology.

Advisors from Stanford and the Karolinska Institute.""",
    """The ask

Raising $28M Series A.

Use of funds: complete IND-enabling package, execute Phase 1 SAD/MAD,
initiate Phase 1b in genetic carriers.

Runway: 30 months to clinical proof of biological activity.""",
]


ONCOLOGY_PAGES: list[str] = [
    """Helix Oncology — precision therapy for KRAS G12C non-small cell lung cancer

Seed round""",
    """HX-220 is a covalent KRAS G12C inhibitor with an IC50 of 11 nM.

In patient-derived xenograft models, HX-220 produced 71% tumour growth
inhibition. Combination with a SHP2 inhibitor increased this to 94%.""",
    """Our objective response rate will exceed that of approved KRAS G12C inhibitors.

Resistance mutations were not observed in our 12-week study.

We are developing a companion diagnostic based on ctDNA.""",
]


def neurogen_pdf() -> bytes:
    return build_pdf(NEUROGEN_PAGES, title="NeuroGen Therapeutics — Series A")


def oncology_pdf() -> bytes:
    return build_pdf(ONCOLOGY_PAGES, title="Helix Oncology — Seed")


def tiny_pdf() -> bytes:
    return build_pdf(["A short document with no scientific content whatsoever."], title="Tiny")
