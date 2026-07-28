"""Beam Therapeutics: base editing, and a deck built largely on track record.

Selected as a benchmark for the ``not_independently_verified`` case. The deck
leans on internal preclinical data, editing-efficiency figures from proprietary
assays, and platform track record -- 37 such claims, none of them contradicted
by anything retrievable. Absence of a public record here is a statement about
what has been published, not about whether the claims are true, and the
scorecard must say so.

Paraphrased from public disclosures for test purposes.
"""

from __future__ import annotations

from app.ingestion.pdf_parser import build_pdf

BEAM_PAGES = [
    """Beam Therapeutics
Precision genetic medicines through base editing.""",
    """Base editing

Base editing makes single base changes without creating double-stranded breaks.
Our editors achieve precise A-to-G and C-to-T conversions.
Avoiding double-stranded breaks reduces the risk of translocations relative to
nuclease-based editing.""",
    """Hematology

BEAM-101 is in clinical development for sickle cell disease and is designed to
increase fetal hemoglobin.
In our studies, BEAM-101 achieved high levels of fetal hemoglobin induction in
patient-derived cells.
Editing efficiency exceeded 90% in our internal assays of hematopoietic stem
cells.""",
    """In vivo liver programmes

BEAM-302 is in Phase 1/2 for alpha-1 antitrypsin deficiency and corrects the
PiZ mutation directly.
BEAM-301 targets the R83C mutation in glycogen storage disease type 1a.
These are the first in vivo base editing programmes to correct a disease-causing
mutation directly.""",
    """Delivery

Our lipid nanoparticle formulations deliver base editors to hepatocytes with
high efficiency.
We have developed proprietary delivery technology for hematopoietic stem cells
that does not require busulfan conditioning in our preclinical models.""",
    """Preclinical data

In non-human primates, a single dose produced durable editing sustained for
more than twelve months.
Off-target editing was not detected above background in our internal
sequencing analyses.
Our platform has produced development candidates faster than industry norms.""",
    """Manufacturing and IP

We operate our own manufacturing facility for clinical supply.
We hold an extensive intellectual property estate covering base editing
compositions and methods.""",
    """Outlook

We expect initial clinical data across our in vivo portfolio.
Base editing is the most precise genetic medicine technology developed to date.
We aim to build a leading genetic medicines company.""",
]


def beam_pdf() -> bytes:
    return build_pdf(BEAM_PAGES, title="Beam Therapeutics")
