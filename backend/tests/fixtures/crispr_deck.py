"""CRISPR Therapeutics: an approved gene-editing therapy plus an early pipeline.

Selected as a benchmark because it mixes the two hardest cases in one deck: a
genuinely approved product (CASGEVY, registry-verifiable and expected to
corroborate) alongside early-stage allogeneic CAR-T and in vivo programmes
whose claims cannot be checked against any registry. A scoring model that
cannot hold both at once will either punish the approval or flatter the
pipeline.

Paraphrased from public disclosures for test purposes.
"""

from __future__ import annotations

from app.ingestion.pdf_parser import build_pdf

CRISPR_PAGES = [
    """CRISPR Therapeutics AG
A gene editing company translating CRISPR/Cas9 into transformative medicines.""",
    """Approved product

CASGEVY (exagamglogene autotemcel) is approved by the U.S. FDA for sickle cell
disease and for transfusion-dependent beta thalassemia.
CASGEVY is the first approved CRISPR/Cas9 gene-edited therapy.
Partnered with Vertex Pharmaceuticals.""",
    """CASGEVY clinical data

In the pivotal trial, 29 of 31 evaluable sickle cell disease patients were free
of severe vaso-occlusive crises for at least 12 consecutive months.
Transfusion independence was achieved in 32 of 35 evaluable beta thalassemia
patients.""",
    """Immuno-oncology pipeline

CTX110 is an allogeneic anti-CD19 CAR-T therapy in Phase 1 for B-cell
malignancies.
CTX130 is an allogeneic anti-CD70 CAR-T therapy in Phase 1 for renal cell
carcinoma and T-cell lymphoma.
CTX112 incorporates potency edits and is in Phase 1/2.""",
    """In vivo programmes

CTX310 targets ANGPTL3 for cardiovascular disease and is in Phase 1.
CTX320 targets lipoprotein(a) and is in Phase 1.
Our lipid nanoparticle delivery achieves durable liver editing after a single
dose.""",
    """Diabetes and regenerative medicine

CTX211 is an allogeneic gene-edited stem cell derived beta cell therapy in
Phase 1 for type 1 diabetes.
Our approach is designed to eliminate the need for chronic immunosuppression.""",
    """Platform

Our proprietary editing platform achieves greater than 90% on-target editing
efficiency in hematopoietic stem cells.
Off-target editing was undetectable by our internal assays.
We hold foundational composition-of-matter intellectual property covering
CRISPR/Cas9 in human cells.""",
    """Outlook

We expect to report additional Phase 1 data across the in vivo portfolio in the
coming year.
Gene editing represents a paradigm shift in the treatment of genetic disease.
We are building the leading gene editing company.""",
]


def crispr_pdf() -> bytes:
    return build_pdf(CRISPR_PAGES, title="CRISPR Therapeutics AG")
