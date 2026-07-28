"""Recursion Pharmaceuticals: a platform company whose claims outrun its clinic.

Selected as a benchmark for the ``plausible_unverified`` case. Most of the
deck asserts capabilities of a computational platform -- dataset scale, model
performance, throughput -- which are neither registry-checkable nor present in
the literature, but which sit comfortably within what is publicly known about
the field. This is the deck that must not be scored as though silence were
contradiction: 26 claims landed at plausible_unverified with nothing arguing
against any of them.

Paraphrased from public disclosures for test purposes.
"""

from __future__ import annotations

from app.ingestion.pdf_parser import build_pdf

RECURSION_PAGES = [
    """Recursion Pharmaceuticals
Decoding biology to industrialize drug discovery.""",
    """The platform

The Recursion Operating System combines wet-lab automation with machine
learning to map cellular biology.
We have generated over 65 petabytes of proprietary biological and chemical
data.
Our automated laboratories run over 2 million experiments each week.""",
    """Maps of biology

Our foundation models are trained on billions of images of perturbed cells.
The Recursion Map encodes relationships across more than 4 trillion predicted
gene and compound interactions.
We identify novel targets without a prior mechanistic hypothesis.""",
    """Compute

BioHive-2 is among the most powerful supercomputers owned by a pharmaceutical
company.
Our inference throughput allows whole-genome scale screening in silico before
committing wet-lab resources.""",
    """Clinical pipeline

REC-994 is in Phase 2 for cerebral cavernous malformation.
REC-2282 is in Phase 2 for neurofibromatosis type 2.
REC-4881 is in Phase 2 for familial adenomatous polyposis.
REC-617 is a CDK7 inhibitor in Phase 1 for solid tumours.""",
    """Partnerships

We have active collaborations with Roche and Genentech in neuroscience and
oncology.
We have a collaboration with Bayer in oncology.
These partnerships validate the industrial applicability of our platform.""",
    """Efficiency

Our platform reduces the time from target identification to development
candidate relative to conventional discovery.
We advance programmes at a fraction of traditional cost per programme.""",
    """Outlook

We expect multiple Phase 2 readouts across the pipeline.
We are building the world's largest and most predictive maps of human biology.
Our objective is to decode biology at industrial scale.""",
]


def recursion_pdf() -> bytes:
    return build_pdf(RECURSION_PAGES, title="Recursion Pharmaceuticals")
