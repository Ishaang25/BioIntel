"""A 50-page biotech corporate deck, used as the performance regression case.

The real BioNTech corporate presentation is what exposed the entity-extraction
bottleneck: 25 pages of dense, figure-heavy content whose *composite* text --
text layer plus tables plus vision-recovered chart values -- came to roughly
88,000 characters.  Handed to one model call that was ~22,000 input tokens,
ran for 542 seconds, hit the 16,000-token output ceiling and came back as
truncated JSON.

This fixture reproduces the shape of that document rather than its bytes:
pipeline breadth (many programmes, each naming its own target, modality and
indication), heavy entity repetition across pages, and enough pages that a
single-call design cannot fit.  Fifty pages is the upper end of the deck size
the pipeline is expected to handle inside two minutes.

Content is written from BioNTech's publicly disclosed pipeline; it is a
paraphrase for test purposes, not a reproduction of the deck.
"""

from __future__ import annotations

from app.ingestion.pdf_parser import build_pdf

#: Programmes named in the deck. Each appears on several pages, which is what
#: makes cross-chunk deduplication load-bearing rather than cosmetic.
PROGRAMS: list[tuple[str, str, str, str]] = [
    ("BNT327", "PD-L1xVEGF-A bispecific antibody", "non-small cell lung cancer", "Phase 3"),
    ("BNT323", "HER2-directed antibody-drug conjugate", "breast cancer", "Phase 3"),
    ("BNT326", "HER3-directed antibody-drug conjugate", "solid tumours", "Phase 2"),
    ("BNT113", "FixVac mRNA cancer immunotherapy", "head and neck cancer", "Phase 2"),
    ("BNT111", "FixVac mRNA cancer immunotherapy", "advanced melanoma", "Phase 2"),
    ("BNT116", "FixVac mRNA cancer immunotherapy", "non-small cell lung cancer", "Phase 1"),
    ("BNT122", "individualised neoantigen mRNA therapy", "colorectal cancer", "Phase 2"),
    ("BNT141", "mRNA-encoded antibody", "gastric cancer", "Phase 1"),
    ("BNT142", "mRNA-encoded CD3 bispecific antibody", "solid tumours", "Phase 1"),
    ("BNT152", "mRNA-encoded interleukin-2", "solid tumours", "Phase 1"),
    ("BNT211", "CLDN6 CAR-T with CARVac amplification", "germ cell tumours", "Phase 1"),
    ("BNT221", "neoantigen-reactive T-cell therapy", "melanoma", "Phase 1"),
]

_HEADER = "BioNTech SE — Corporate Presentation"


def _title_page() -> str:
    return f"""{_HEADER}

Immunotherapy for cancer and infectious disease
Mainz, Germany

Forward-looking statements
This presentation contains forward-looking statements within the meaning of
applicable securities law, including statements about our development plans,
the timing and results of clinical trials, the potential of our product
candidates, our manufacturing capacity and our collaborations. Forward-looking
statements are subject to risks and uncertainties, including the risk that
clinical results observed to date are not replicated in larger or randomised
studies, that regulatory authorities require additional data, and that our
collaborators change their development priorities. Actual results may differ
materially. We undertake no obligation to update these statements.

Our strategy
We combine multiple therapeutic modalities against the same disease biology:
mRNA immunotherapy, engineered cell therapy, and targeted antibodies and
antibody-drug conjugates. Our objective is to select, for each indication, the
modality most likely to produce a durable response, and to combine modalities
where the biology supports it.

We believe that individualised cancer immunotherapy will become a standard
component of treatment for solid tumours, and we are building the discovery,
manufacturing and clinical infrastructure required to deliver it at scale."""


def _platform_page(index: int, name: str, description: str, assets: str) -> str:
    return f"""Platform {index}: {name}

{description}

Assets built on this platform: {assets}

Design principles
Our platform approach is designed to allow rapid iteration across indications.
Once a construct format is validated, changing the encoded antigen does not
require re-engineering the delivery system, the manufacturing process or the
analytical release assays. We believe the modular nature of this technology is
a durable competitive advantage.

Translational package
Each construct is characterised in a standard cascade before it enters the
clinic: in vitro expression in primary human cells, antigen presentation
measured by ELISpot and intracellular cytokine staining, biodistribution in
rodents, and tumour growth inhibition in syngeneic and xenograft models.
Immunogenicity is confirmed by tetramer staining of antigen-specific CD8+
T cells in peripheral blood.

Delivery
Lipid nanoparticle and RNA-lipoplex formulations are selected for the target
tissue. The RNA-LPX carrier is designed for systemic administration with
preferential uptake by antigen-presenting cells in the spleen and lymph nodes,
producing a type-I interferon-driven immune response.

Analytics
Release testing covers RNA integrity, capping efficiency, poly(A) tail length,
residual double-stranded RNA, encapsulation efficiency and particle size."""


def _program_page(code: str, modality: str, indication: str, stage: str) -> str:
    return f"""{code} — {modality}

Indication: {indication}
Development stage: {stage}
Target population: patients with advanced or metastatic disease who have
progressed on or after standard-of-care therapy.

Mechanism
{code} is designed to engage its target on tumour cells while sparing normal
tissue. Target expression is assessed by immunohistochemistry on archival or
fresh tumour biopsy. Binding affinity was characterised by surface plasmon
resonance, and cytotoxicity in a panel of target-positive and target-negative
cell lines established a selectivity window.

Preclinical package
In cell-derived and patient-derived xenograft models, {code} produced
dose-dependent tumour growth inhibition versus vehicle control. Combination
with checkpoint blockade increased the depth of response in syngeneic models.
Repeat-dose toxicology in non-human primates supported the starting dose
selected for the first-in-human study.

Clinical plan
We are enrolling patients in a {stage} study of {code} in {indication}. The
study includes a dose-escalation portion followed by expansion cohorts in
biomarker-selected populations. The primary endpoints are safety and
tolerability; secondary endpoints include objective response rate by RECIST
1.1, duration of response and progression-free survival.

Biomarker strategy
Patients are characterised for target expression by immunohistochemistry, for
tumour mutational burden by whole-exome sequencing, and for on-treatment
response by serial circulating tumour DNA. Peripheral immune monitoring
includes ELISpot, flow cytometry for activation markers, and a cytokine panel
covering interferon-gamma, interleukin-6 and tumour necrosis factor alpha.

Combination rationale
The combination strategy for {code} is built on the observation that response
to checkpoint blockade in {indication} is limited by the number of tumour
antigen-specific T cells available to be de-repressed. {code} is intended to
raise that number, which is why the expansion cohorts pair it with a PD-1 or
PD-L1 directed agent rather than testing it as monotherapy.

Development risk
Target expression heterogeneity, on-target off-tumour toxicity and the
durability of response beyond twelve months remain open questions for this
programme. The preclinical models used are imperfect predictors of human
response, and the doses at which efficacy was observed in those models do not
translate directly to the clinical dose range."""


def _data_page(code: str, indication: str, orr: int, n: int) -> str:
    return f"""{code} clinical update — {indication}

Efficacy
In the ongoing study, the confirmed objective response rate was {orr}% (n={n}).
The disease control rate was {orr + 28}%. Median progression-free survival has
not been reached at the current follow-up. Responses were observed across dose
levels, including in patients with prior checkpoint-inhibitor exposure.

Safety
Treatment-related adverse events were predominantly grade 1-2 and consisted of
pyrexia, chills, fatigue and infusion-related reactions. Grade 3 or higher
treatment-related events occurred in a minority of patients. No dose-limiting
toxicity was observed at the doses evaluated to date, and no treatment-related
deaths have been reported.

Biomarkers
Biomarker analysis showed target engagement measured by circulating tumour DNA
in evaluable patients. Immunogenicity was assessed by ELISpot against the
encoded antigens, and antigen-specific T-cell responses were detected in the
majority of evaluable patients after the priming series. Reduction in ctDNA at
cycle three was associated with radiographic response in this cohort.

Patient population
The cohort comprised patients with advanced or metastatic {indication} who had
received at least one prior line of systemic therapy. Roughly half had received
prior checkpoint blockade. Baseline performance status was 0 or 1. Patients
with untreated central nervous system metastases, active autoimmune disease or
prior organ transplantation were excluded, which limits how far these results
generalise to an unselected population.

Comparison to standard of care
Historical response rates for this population in {indication} are in a similar
range, and no cross-trial comparison is reliable given differences in prior
therapy, biomarker selection and response assessment. A randomised study is
required to establish whether {code} improves outcomes.

Interpretation
These interim data are from a small, non-randomised cohort without a control
arm and should be interpreted with caution. Response assessment was by
investigator review rather than blinded independent central review. Follow-up
is short relative to the natural history of the disease, and the confidence
interval around the reported response rate is wide at this sample size. The
data are not sufficient to establish a benefit over standard of care in
{indication}."""


def _pipeline_page(rows: list[tuple[str, str, str, str]], part: int) -> str:
    lines = [
        f"Oncology pipeline ({part} of 2)",
        "",
        "Programme | Modality | Indication | Stage",
    ]
    lines += [
        f"{code} | {modality} | {indication} | {stage}"
        for code, modality, indication, stage in rows
    ]
    lines += [
        "",
        "Notes on the table",
        "Stage reflects the most advanced study in which the programme is currently",
        "enrolling. Programmes shown at Phase 2 have completed dose escalation and are",
        "enrolling expansion cohorts; they have not completed a randomised comparison",
        "against standard of care. Indication reflects the lead indication only; several",
        "programmes are being evaluated in additional tumour types under the same",
        "protocol. Partnered programmes are subject to our collaborators' development",
        "decisions and the timing shown may change.",
        "",
        "None of the oncology programmes listed has been approved by any regulatory",
        "authority in any jurisdiction.",
    ]
    return "\n".join(lines)


def _infectious_disease_page() -> str:
    return """Infectious disease and partnered programmes

COMIRNATY, our COVID-19 mRNA vaccine partnered with Pfizer, is approved in the
United States and the European Union. Variant-adapted COMIRNATY formulations
have been authorised for seasonal use following review of immunogenicity data
against circulating strains.

Our infectious-disease pipeline applies the same mRNA platform to influenza,
mpox, tuberculosis, herpes simplex virus and malaria. The influenza programme
encodes haemagglutinin and neuraminidase antigens from the strains recommended
for the season. The tuberculosis and malaria programmes are supported by
collaboration with public-health partners and are being developed with an
access-oriented pricing model.

Programme status
Influenza: clinical stage, immunogenicity data in healthy adults.
Mpox: clinical stage.
Tuberculosis: clinical stage, supported by a public-health collaboration.
Herpes simplex virus: clinical stage.
Malaria: clinical stage.

Regulatory note
Other than COMIRNATY, none of the infectious-disease candidates described here
has been approved by any regulatory authority. Statements about their potential
are forward-looking."""


def _partnership_page() -> str:
    return """Collaborations

Pfizer — COVID-19 vaccine collaboration covering development, manufacturing and
commercialisation of COMIRNATY, with profits shared under the collaboration
agreement.

Genmab — bispecific antibody collaboration applying Genmab's DuoBody technology
to jointly selected targets.

Regeneron — clinical collaboration evaluating BNT122, our individualised
neoantigen mRNA therapy, in combination with cemiplimab.

Duality Biologics — antibody-drug conjugate collaboration covering BNT323 and
BNT326, under which we hold development and commercialisation rights in
specified territories.

OncoC4 — collaboration on an anti-CTLA-4 antibody being evaluated in
combination with our checkpoint-directed programmes.

Academic collaborations with the University of Mainz, TRON gGmbH and the
Helmholtz Institute support target discovery and translational research.

Each collaboration allocates development responsibility, cost sharing and
commercial rights differently; economics vary materially between agreements.
We are committed to expanding our partnership footprint."""


def _financials_page() -> str:
    return """Financial position

Cash, cash equivalents and security investments provide runway through our
planned late-stage readouts. Revenue is currently derived predominantly from
the COVID-19 vaccine collaboration and is subject to seasonal demand and
government procurement decisions.

We expect research and development expenses to increase as our Phase 3
programmes enrol, driven by clinical trial costs, manufacturing of clinical
supply and headcount in clinical development and regulatory affairs.

Capital allocation priorities are, in order: advancing the late-stage oncology
programmes, sustaining the individualised manufacturing capability, and
selective business development.

Market context
The global oncology therapeutics market is projected to exceed $400 billion.
The addressable population for the lead indications in our pipeline includes
several hundred thousand patients annually across major markets. These are
market estimates, not forecasts of our revenue; our share of any market depends
on clinical results, approval and competition."""


def _manufacturing_page() -> str:
    return """Manufacturing

We operate mRNA manufacturing sites in Marburg and Mainz, and a modular
facility in Kigali, Rwanda, intended to support regional supply of mRNA-based
vaccines.

Individualised manufacturing
Our individualised neoantigen process sequences the patient's tumour and
matched normal tissue, predicts neoantigens computationally, and manufactures a
patient-specific mRNA therapy within a defined turnaround time. The process is
release-tested per patient against identity, potency, purity and sterility
specifications.

Scale
Capacity is sufficient to supply our current clinical programmes and to support
initial commercial launch of a lead programme, subject to approval. Scaling the
individualised process to commercial volumes requires further automation of the
sequencing-to-release workflow, which is in development.

We believe our in-house manufacturing is a best-in-class capability and a
significant barrier to entry for individualised therapy."""


def _team_page() -> str:
    return """Leadership and scientific base

Prof. Ugur Sahin, M.D. — Chief Executive Officer and co-founder. Physician and
immunologist; principal investigator on individualised cancer immunotherapy
studies.

Dr. Ozlem Tureci, M.D. — Chief Medical Officer and co-founder. Physician and
immunologist; work on tumour antigen discovery underpins several programmes.

Dr. Sierk Poetting — Chief Operating Officer.

Scientific collaborations with the University of Mainz, TRON gGmbH and academic
centres in Germany and the United States support target discovery,
immunomonitoring and translational science.

Our clinical development organisation runs studies across sites in Europe, the
United States and Asia-Pacific, with in-house immunomonitoring laboratories
performing the ELISpot, tetramer and ctDNA assays reported in this deck."""


def _competition_page() -> str:
    return """Competitive landscape

Checkpoint inhibitors are the standard of care in most of the indications we
target, and any new agent will be evaluated in combination with or against
them. Several companies are developing PD-L1xVEGF-A bispecific antibodies, and
the class has produced encouraging early data; randomised comparisons will
determine whether the bispecific format is superior to the combination of its
component mechanisms.

In HER2-directed antibody-drug conjugates, an approved agent has established a
high efficacy bar in breast cancer and is expanding into HER2-low populations.
Differentiation for BNT323 will depend on the therapeutic index and on activity
in populations that have progressed on the approved agent.

In individualised neoantigen therapy the field is small; the principal
comparators are also mRNA-based and are being developed with checkpoint
inhibitor combinations. Manufacturing turnaround time and cost of goods, not
only efficacy, are likely to determine which approach reaches routine use.

We cannot determine from currently available public data whether any of our
programmes will prove best in class."""


def _outlook_page() -> str:
    return """Outlook

Near-term milestones
We plan to initiate additional registrational studies next year. We are on
track to report randomised data for BNT327 in non-small cell lung cancer.
Additional dose-expansion data are expected from the FixVac and cell-therapy
programmes. We expect to complete enrolment in the lead breast-cancer study.

What would change our view
A randomised readout that fails to separate from control in the lead
programme, evidence that responses are not durable beyond twelve months, or a
safety signal that narrows the therapeutic index would each materially change
our development priorities.

Our goal is to become a leading immunotherapy company. We believe our approach
represents a paradigm shift in cancer treatment.

These statements are forward-looking and are not a prediction of results."""


def biontech_pages() -> list[str]:
    """Build the ~50-page deck."""
    pages: list[str] = [_title_page()]

    pages.append(
        _platform_page(
            1,
            "mRNA immunotherapy",
            "Our mRNA platforms encode tumour antigens, antibodies and cytokines. "
            "Uridine mRNA, nucleoside-modified mRNA and self-amplifying mRNA formats "
            "are formulated in lipid nanoparticles or a liposomal RNA-LPX carrier.",
            "BNT111, BNT113, BNT116, BNT122, BNT141, BNT142, BNT152",
        )
    )
    pages.append(
        _platform_page(
            2,
            "Cell therapy",
            "Chimeric antigen receptor T cells directed at CLDN6, amplified in vivo by "
            "our CARVac mRNA vaccine, and neoantigen-reactive autologous T cells.",
            "BNT211, BNT221",
        )
    )
    pages.append(
        _platform_page(
            3,
            "Targeted antibodies and ADCs",
            "Bispecific antibodies against PD-L1 and VEGF-A, and antibody-drug conjugates "
            "directed at HER2 and HER3 using a topoisomerase-I inhibitor payload.",
            "BNT327, BNT323, BNT326",
        )
    )

    pages.append(_pipeline_page(PROGRAMS[:6], part=1))
    pages.append(_pipeline_page(PROGRAMS[6:], part=2))

    for code, modality, indication, stage in PROGRAMS:
        pages.append(_program_page(code, modality, indication, stage))

    for offset, (code, _modality, indication, _stage) in enumerate(PROGRAMS):
        pages.append(_data_page(code, indication, orr=24 + offset * 3, n=18 + offset * 5))

    pages.append(_infectious_disease_page())
    pages.append(_partnership_page())
    pages.append(_manufacturing_page())
    pages.append(_competition_page())
    pages.append(_financials_page())
    pages.append(_team_page())
    pages.append(_outlook_page())

    # Programme detail repeated at the back of the deck, as a real corporate
    # presentation does with its appendix: the same entities under different
    # slide titles, which is exactly what stresses cross-chunk merging.
    for code, modality, indication, stage in PROGRAMS:
        pages.append(
            f"""Appendix — {code}

{code} ({modality}) is being developed in {indication}. Current stage: {stage}.

Preclinical characterisation included binding affinity by surface plasmon
resonance, cytotoxicity assays across target-positive and target-negative cell
lines, biodistribution studies, and xenograft efficacy studies in models
selected for target expression.

Clinical pharmacology
Exposure was dose-proportional over the range studied. Anti-drug antibodies
were assessed at each cycle and did not correlate with loss of exposure.

Regulatory status
{code} has not been approved by any regulatory authority. Statements about the
potential of {code} are forward-looking and subject to substantial development
and regulatory risk."""
        )

    return pages


def biontech_pdf() -> bytes:
    return build_pdf(biontech_pages(), title=_HEADER)
