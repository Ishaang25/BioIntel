"""The Moderna JPM 2025 deck, as a benchmark document.

Statements are taken from the real presentation that produced the 24.1/100
"unsupported" result -- the case that exposed the scoring bug. Each page
exercises a different failure mode: an approved product, a registry-checkable
regulatory claim, a promotional statement, and forward-looking guidance.

Paraphrased for test purposes; not a reproduction of the deck.
"""

from __future__ import annotations

from app.ingestion.pdf_parser import build_pdf

MODERNA_PAGES = [
    """Moderna, Inc.
Moderna was founded and built to use nature's information molecule, mRNA, to
treat and prevent disease.""",
    """2024 in review

Approval of mRESVIA, our 2nd commercial product.
mRESVIA U.S. FDA approval for ages 60+.
Four positive Phase 3 readouts.
Entering 2025 with two approved products in the U.S.""",
    """Platform performance

Moderna's rate of success with our platform technology is higher than industry
standard. Our Phase 1 probability of success is 62% versus 35% for industry.""",
    """Regulatory milestones

Next-gen COVID is filed with a PDUFA date of May 30, 2025.
Flu + COVID combo 50+ is filed.
3 Biologics License Applications (BLAs) filed.""",
    """Late-stage pipeline

CMV vaccine mRNA-1647 is in Phase 3 efficacy testing.
Seasonal flu mRNA-1010 is in Phase 3 efficacy testing.
PA program mRNA-3927 is in a registrational efficacy study.""",
    """Oncology and rare disease

Adjuvant melanoma program mRNA-4157 is partnered and in development.
Cystic fibrosis program mRNA-3692 / VX-522 is partnered with Vertex.
Our platform is a revolutionary breakthrough in medicine.""",
    """Outlook

We will file additional programs in 2026.
The total addressable market for respiratory vaccines exceeds $14 billion.
Our objective is to become the leading mRNA medicines company.""",
]


def moderna_pdf() -> bytes:
    return build_pdf(MODERNA_PAGES, title="Moderna, Inc.")
