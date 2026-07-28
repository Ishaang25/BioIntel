"""Structured-output contracts for every LLM interaction.

These Pydantic models are the *only* way information enters the system from a
language model.  Each one is compiled to a strict JSON schema (see
:mod:`app.llm.json_schema`) so the provider is constrained at decode time, and
then re-validated locally.

Conventions that reduce hallucination:

* Anything sourced from the document carries a ``verbatim_quote`` that we
  verify against the extracted text.
* Confidence is always explicit and always 0-1.
* Fields the model cannot ground are nullable; the prompts instruct the model
  to emit ``null`` rather than guess.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    ClaimCategory,
    ClaimType,
    ConfidenceLevel,
    CorroborationStatus,
    EntityType,
    EvidenceTier,
    PublicationType,
    QuestionPriority,
    RiskCategory,
    RiskSeverity,
    Stance,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, str_strip_whitespace=True)


# ==================================================== page understanding ===
class VisualElement(StrictModel):
    kind: str = Field(
        description=(
            "One of: bar_chart, line_chart, scatter_plot, kaplan_meier, waterfall, "
            "forest_plot, western_blot, micrograph, pathway_diagram, mechanism_diagram, "
            "timeline, table, flow_diagram, molecular_structure, logo, photo, other."
        )
    )
    description: str = Field(
        description="What the visual shows, in one or two sentences. Describe only what is visible."
    )
    axis_labels: list[str] = Field(
        description="Axis or column labels exactly as printed. Empty list if none are legible."
    )
    legend_entries: list[str] = Field(
        description="Legend/series names exactly as printed. Empty list if none."
    )
    is_data_bearing: bool = Field(
        description="True if the element presents experimental or clinical data (not decoration)."
    )


class DataPoint(StrictModel):
    label: str = Field(description="What the number measures, as printed on the page.")
    value: str = Field(
        description="The value exactly as printed, including sign and separators (e.g. '62%', '3.2')."
    )
    unit: str | None = Field(description="Unit as printed, or null if none is shown.")
    context: str = Field(
        description="Series/arm/timepoint this value belongs to, as printed. Empty string if unclear."
    )
    is_read_from_axis: bool = Field(
        description=(
            "True when the value was estimated by reading a chart rather than printed as text. "
            "Estimated values must be flagged."
        )
    )


class ExtractedTable(StrictModel):
    title: str = Field(description="Table caption or heading, or an empty string.")
    markdown: str = Field(
        description="The table rendered as GitHub-flavoured markdown, preserving printed values."
    )


class PageUnderstandingOut(StrictModel):
    """Multimodal reading of a single deck page."""

    slide_title: str | None = Field(description="The page/slide title as printed, or null.")
    summary: str = Field(
        description="2-4 sentences describing what this page communicates. Only what is on the page."
    )
    recovered_text: str = Field(
        description=(
            "All legible text on the page transcribed verbatim in reading order. "
            "Required for scanned pages; for digital pages transcribe only text that is "
            "part of images/charts and therefore missing from the text layer."
        )
    )
    visual_elements: list[VisualElement] = Field(
        description="Every distinct figure, chart or diagram on the page. Empty list if none."
    )
    data_points: list[DataPoint] = Field(
        description="Quantitative values shown on the page. Empty list if none."
    )
    tables: list[ExtractedTable] = Field(
        description="Tabular data visible on the page. Empty list if none."
    )
    contains_scientific_content: bool = Field(
        description="True if the page presents scientific/clinical substance rather than business content."
    )
    legibility: float = Field(
        ge=0.0,
        le=1.0,
        description="How legible the page was, 0 (unreadable) to 1 (perfectly clear).",
    )


# =========================================================== company profile ===
class PipelineProgram(StrictModel):
    name: str = Field(description="Program or asset name as printed.")
    indication: str | None = Field(description="Target indication, or null if not stated.")
    modality: str | None = Field(description="Therapeutic modality, or null if not stated.")
    stage: str | None = Field(
        description="Development stage as stated (e.g. 'Discovery', 'IND-enabling', 'Phase 1')."
    )
    target: str | None = Field(description="Molecular target, or null if not stated.")


class TeamMember(StrictModel):
    name: str = Field(description="Person's name as printed.")
    role: str | None = Field(description="Stated role/title, or null.")
    credentials: str | None = Field(description="Stated degrees/affiliations, or null.")


class CompanyProfileOut(StrictModel):
    company_name: str | None = Field(description="Company name as printed, or null.")
    one_liner: str | None = Field(
        description="The company's own one-sentence description, or null."
    )
    founded_year: int | None = Field(description="Founding year if stated, else null.")
    headquarters: str | None = Field(description="Location if stated, else null.")
    company_stage: str | None = Field(
        description="Funding stage as stated (e.g. 'Seed', 'Series A'), else null."
    )
    lead_program: str | None = Field(description="Lead asset name, or null.")
    lead_indication: str | None = Field(description="Lead indication, or null.")
    modality: str | None = Field(description="Primary modality, or null.")
    development_stage: str | None = Field(
        description="Furthest development stage claimed for the lead program, or null."
    )
    pipeline: list[PipelineProgram] = Field(description="All programs shown. Empty list if none.")
    team: list[TeamMember] = Field(description="Named team members. Empty list if none.")
    total_raised: str | None = Field(description="Capital raised to date as stated, or null.")
    current_raise: str | None = Field(description="Round being raised as stated, or null.")
    use_of_funds: str | None = Field(description="Stated use of proceeds, or null.")
    partnerships: list[str] = Field(description="Named partners/collaborators. Empty if none.")
    ip_position: str | None = Field(description="Stated IP position, or null.")
    business_model: str | None = Field(description="Stated business model, or null.")
    source_pages: list[int] = Field(description="Page numbers that informed this profile.")


# ================================================================ entities ===
class ExtractedEntity(StrictModel):
    entity_type: EntityType = Field(description="The kind of scientific entity.")
    name: str = Field(description="The entity as written in the deck.")
    canonical_name: str | None = Field(
        description=(
            "Standard scientific name if you are confident of it (e.g. 'KRAS' for 'K-ras'), "
            "otherwise null. Do not invent identifiers."
        )
    )
    aliases: list[str] = Field(description="Other names used in the deck for the same entity.")
    description: str | None = Field(
        description="One-sentence description of the entity, or null if unsure."
    )
    role_in_program: str | None = Field(
        description="How the deck says this entity relates to the company's program, or null."
    )
    source_pages: list[int] = Field(description="Pages where this entity appears.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence that this entity is correct.")


class EntityExtractionOut(StrictModel):
    entities: list[ExtractedEntity] = Field(
        description="All distinct scientific entities in the document. Merge duplicates."
    )


# ================================================================== claims ===
class QuantitativeDetail(StrictModel):
    metric: str = Field(
        description="What was measured (e.g. 'IC50', 'ORR', 'tumour volume reduction')."
    )
    value: str = Field(description="The value exactly as stated.")
    unit: str | None = Field(description="Unit as stated, or null.")
    comparator: str | None = Field(description="Control/comparator arm as stated, or null.")
    sample_size: str | None = Field(description="n as stated, or null.")
    p_value: str | None = Field(description="p-value or CI as stated, or null.")
    model_system: str | None = Field(
        description="Experimental system as stated (e.g. 'MPTP mouse model'), or null."
    )


class ExtractedClaim(StrictModel):
    statement: str = Field(
        description=(
            "A self-contained restatement of one assertion the company makes, understandable "
            "without the deck. Do not add facts that are not in the source text."
        )
    )
    verbatim_quote: str = Field(
        description=(
            "The exact contiguous text from the page that this claim is based on, copied "
            "character for character. Never paraphrase here."
        )
    )
    page_number: int = Field(description="1-based page number the quote came from.")
    from_visual: bool = Field(
        description="True if the quote came from a chart/figure reading rather than the text layer."
    )
    claim_type: ClaimType = Field(
        description=(
            "What KIND of assertion this is. This is the most consequential field you "
            "produce: it decides how the claim is checked and scored. A statement of "
            "regulatory fact, an experimental result and a revenue projection are "
            "different kinds of object and must not be labelled the same way. Choose "
            "'marketing', 'corporate_vision', 'strategic_objective', 'forward_looking' or "
            "'financial_guidance' for anything that cannot be true or false today -- those "
            "are reported but excluded from credibility scoring."
        )
    )
    category: ClaimCategory = Field(description="Which aspect of the business the claim concerns.")
    claimed_evidence_tier: EvidenceTier = Field(
        description=(
            "The strongest kind of evidence the deck says supports this claim. "
            "Use 'none_stated' when the deck asserts the claim without describing data."
        )
    )
    quantitative: list[QuantitativeDetail] = Field(
        description="Numbers attached to this claim. Empty list if the claim is qualitative."
    )
    entity_names: list[str] = Field(
        description="Names of scientific entities this claim is about, as written in the deck."
    )
    is_scientific: bool = Field(
        description="True for scientific/clinical claims; false for purely commercial ones."
    )
    is_falsifiable: bool = Field(
        description="True if the claim could in principle be checked against evidence."
    )
    hedging_language: bool = Field(
        description="True if the deck hedges (e.g. 'may', 'could', 'potential', 'designed to')."
    )
    is_thesis_critical: bool = Field(
        description="True if an investment thesis would materially change were this claim false."
    )
    importance: float = Field(
        ge=0.0, le=1.0, description="How central this claim is to the company's scientific story."
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Your confidence that this claim was extracted correctly."
    )


class ClaimExtractionOut(StrictModel):
    claims: list[ExtractedClaim] = Field(
        description="Distinct, non-overlapping claims found in the provided pages."
    )


# =============================================================== retrieval ===
class LiteratureQuery(StrictModel):
    query: str = Field(
        description=(
            "A search query. For PubMed use boolean syntax with field tags where helpful "
            "(e.g. 'LRRK2[tiab] AND Parkinson Disease[MeSH Terms]'). Keep it under 25 words."
        )
    )
    intent: str = Field(
        description="One of: confirm, refute, background, competitive_landscape, safety, endpoint_validation."
    )
    rationale: str = Field(description="Why this query will surface decision-relevant evidence.")


class QueryPlanOut(StrictModel):
    pubmed_queries: list[LiteratureQuery] = Field(
        description="1-4 PubMed queries. Include at least one designed to find contradictory evidence."
    )
    trial_conditions: list[str] = Field(
        description="0-3 condition terms for a ClinicalTrials.gov search, or an empty list."
    )
    trial_interventions: list[str] = Field(
        description="0-3 intervention terms for a ClinicalTrials.gov search, or an empty list."
    )


# ============================================================ adjudication ===
class AdjudicationOut(StrictModel):
    stance: Stance = Field(
        description=(
            "How the evidence record bears on the claim. 'unrelated' when the record is "
            "about a different target, indication or question."
        )
    )
    relevance: float = Field(
        ge=0.0, le=1.0, description="How on-topic the record is for this specific claim."
    )
    strength: float = Field(
        ge=0.0,
        le=1.0,
        description="How decisively the record settles the claim, given its study design and directness.",
    )
    supporting_quote: str = Field(
        description=(
            "One verbatim sentence copied from the supplied abstract that justifies the stance. "
            "Empty string only when the stance is 'unrelated'."
        )
    )
    rationale: str = Field(
        description="1-2 sentences explaining the stance. Reference only the supplied record."
    )
    caveats: list[str] = Field(
        description="Limitations that weaken the inference (species, dose, population, design)."
    )
    study_design: PublicationType = Field(
        description="Your classification of the record's study design based on the supplied metadata."
    )
    addresses_claim_directly: bool = Field(
        description=(
            "True only when the record is about the same intervention AND the same question "
            "as the claim. A paper about the same disease but a different drug is topically "
            "related, not directly on point; mark it false."
        )
    )


class BatchAdjudicationItem(AdjudicationOut):
    evidence_ref: str = Field(
        description="The reference id of the evidence record, copied exactly from the input."
    )


class BatchAdjudicationOut(StrictModel):
    adjudications: list[BatchAdjudicationItem] = Field(
        description="Exactly one entry per supplied evidence record, in the same order."
    )


# ============================================================== assessment ===
class EvidenceComparison(StrictModel):
    """How two or more retrieved records relate to each other."""

    topic: str = Field(
        description="The specific question the records bear on (e.g. 'durability beyond 12 months')."
    )
    agreement: str = Field(
        description=(
            "What the records agree on, citing them by reference id. Empty string if they "
            "do not overlap enough to agree on anything."
        )
    )
    disagreement: str = Field(
        description=(
            "Where they diverge and why (different population, dose, endpoint, follow-up). "
            "Empty string if there is no genuine disagreement -- do not manufacture one."
        )
    )
    quality_contrast: str = Field(
        description=(
            "How the records differ in evidential weight: study design, sample size, "
            "randomisation, blinding, sponsor. Name which is stronger and why."
        )
    )
    translatability: str = Field(
        description=(
            "What these records do and do not license you to conclude about the company's "
            "claim: species, population, endpoint and dose gaps."
        )
    )


class ClaimVerdictOut(StrictModel):
    corroboration_status: CorroborationStatus = Field(
        description=(
            "The outcome of checking this claim. Use 'contradicted' ONLY when retrieved "
            "evidence genuinely disagrees. Use 'insufficient_evidence' when the search "
            "returned nothing on point, and 'not_independently_verified' when the claim is "
            "the kind of thing only a regulator or the company could confirm. Absence of "
            "evidence is not evidence against."
        )
    )
    confidence: ConfidenceLevel = Field(
        description="How confident you are in this determination, given what was retrieved."
    )
    confidence_reason: str = Field(
        description=(
            "One sentence explaining the confidence level: what would have to be true, or "
            "what you would need to see, to raise it."
        )
    )
    verdict: str = Field(
        description=(
            "2-4 sentences for an investment committee. Separate three registers explicitly: "
            "what the company asserts, what the retrieved literature shows, and what BioIntel "
            "infers from the gap. Cite evidence by reference id."
        )
    )
    comparisons: list[EvidenceComparison] = Field(
        description=(
            "Comparative synthesis of the retrieved records -- not a summary of each. "
            "Empty list when fewer than two records bear on the claim."
        )
    )
    key_uncertainties: list[str] = Field(
        description="Specific, testable unknowns that prevent a firmer conclusion. 1-4 items."
    )
    novelty: float = Field(
        ge=0.0,
        le=1.0,
        description="0 = well-established in the literature, 1 = no external precedent found.",
    )
    translational_gap: str | None = Field(
        description=(
            "The gap between the evidence tier offered and what the claim implies, or null "
            "if there is no material gap."
        )
    )
    so_what: str = Field(
        description=(
            "One sentence: what this claim's status means for the investment decision. Not a "
            "restatement of the verdict -- the consequence of it."
        )
    )


# =================================================== risks & questions ===
class RiskOut(StrictModel):
    title: str = Field(description="Short risk headline (under 12 words).")
    category: RiskCategory = Field(description="Risk domain.")
    severity: RiskSeverity = Field(description="How material this risk is to the investment.")
    description: str = Field(
        description="2-3 sentences. State the evidence basis; never assert facts not supplied."
    )
    related_claim_refs: list[str] = Field(
        description="Claim reference ids this risk derives from, copied exactly from the input."
    )


class DiligenceQuestionOut(StrictModel):
    question: str = Field(
        description=(
            "A specific, answerable question for the company. Must be technical and "
            "decision-relevant, not generic."
        )
    )
    rationale: str = Field(description="Why this matters to the investment decision.")
    priority: QuestionPriority = Field(description="How urgent an answer is.")
    category: RiskCategory = Field(description="Domain the question probes.")
    what_good_looks_like: str = Field(
        description="What a satisfactory answer would contain, so the analyst can judge the response."
    )
    related_claim_refs: list[str] = Field(
        description="Claim reference ids this question relates to, copied exactly from the input."
    )


class RisksAndQuestionsOut(StrictModel):
    risks: list[RiskOut] = Field(description="Material scientific risks. 3-10 items.")
    questions: list[DiligenceQuestionOut] = Field(
        description="Diligence questions ordered by importance. 8-15 items."
    )


# ==================================================== scientific reasoning ===
class ModalityPrecedent(StrictModel):
    """What history says about this therapeutic approach."""

    modality: str = Field(description="The modality or approach, as the deck describes it.")
    has_approved_precedent: bool = Field(
        description="True only if the supplied evidence shows an approved product using this approach."
    )
    precedent_summary: str = Field(
        description=(
            "What the supplied evidence shows about this approach succeeding or failing "
            "before. Cite records by reference id. If nothing was retrieved, say so."
        )
    )
    notable_failures: list[str] = Field(
        description=(
            "Programmes at this target or using this modality that failed, per the supplied "
            "evidence only. Empty list if none appear in the records."
        )
    )


class ScientificAssessmentOut(StrictModel):
    """The reasoning a biotech investor applies before the numbers."""

    biological_plausibility: str = Field(
        description=(
            "Is the proposed mechanism consistent with what the supplied literature "
            "establishes about this target and pathway? State the specific biology, not a "
            "generic judgement."
        )
    )
    plausibility_confidence: ConfidenceLevel = Field(
        description="Confidence in the plausibility assessment."
    )
    modality_precedent: ModalityPrecedent = Field(
        description="Whether this therapeutic approach has worked before."
    )
    first_in_class: str = Field(
        description=(
            "Is this first-in-class, best-in-class, or a follower? Justify from the retrieved "
            "competitive records. Say 'cannot determine from the retrieved evidence' rather "
            "than guessing."
        )
    )
    differentiation: str = Field(
        description=(
            "What would have to be true for this asset to beat the standard of care or the "
            "leading competitor, and does the supplied evidence support it?"
        )
    )
    de_risking_achieved: str = Field(
        description=(
            "How much technical risk has actually been retired, in stages: target validated? "
            "mechanism shown in humans? dose established? efficacy demonstrated?"
        )
    )
    partnerability: str = Field(
        description=(
            "Would a pharmaceutical partner plausibly license this at its current stage, and "
            "what would they need to see first? Reason from precedent in the retrieved records."
        )
    )
    milestones_that_matter: list[str] = Field(
        description=(
            "The 3-5 specific readouts or events that would most change the investment view, "
            "in order of impact."
        )
    )
    key_failure_mode: str = Field(
        description=(
            "The single most likely way this programme fails scientifically, stated concretely."
        )
    )


class DimensionCommentaryOut(StrictModel):
    dimension: str = Field(
        description="The scorecard dimension key, copied exactly from the input."
    )
    so_what: str = Field(
        description=(
            "One or two sentences on what this score means for the investment decision. Do "
            "not restate the number; explain its consequence."
        )
    )
    what_would_change_it: str = Field(
        description="The specific evidence that would move this score materially."
    )


class ScorecardCommentaryOut(StrictModel):
    """Narration of the computed scorecard. The model never sets the numbers."""

    headline: str = Field(
        description=(
            "One sentence an IC chair could read aloud: the state of the scientific case, "
            "using the supplied overall score. Do not recompute it."
        )
    )
    commentary: list[DimensionCommentaryOut] = Field(
        description="One entry per supplied dimension, in the same order."
    )
    decisive_factors: list[str] = Field(
        description=(
            "The 2-4 findings that most determine the recommendation, each stated as a "
            "consequence rather than an observation."
        )
    )


# ================================================================= report ===
class ReportSectionOut(StrictModel):
    heading: str = Field(description="Section heading.")
    body_markdown: str = Field(
        description=(
            "Section body in markdown. Cite claims as [C#] and evidence as [E#] using the "
            "reference ids supplied. Every factual sentence must carry a citation or be "
            "explicitly marked as BioIntel inference. Analysis, not summary: the reader has "
            "the deck already."
        )
    )
    so_what: str = Field(
        description=(
            "The section's bottom line for an investment committee, in one or two sentences. "
            "What should the reader do or believe differently having read it? This is the "
            "most-read line in the section; it must carry an argument, not a summary."
        )
    )
    confidence: ConfidenceLevel = Field(
        description="How confident this section's conclusions are, given the evidence behind them."
    )
    confidence_reason: str = Field(
        description="One sentence explaining the confidence level for this section."
    )
    citation_refs: list[str] = Field(
        description="All [C#]/[E#] reference ids used in this section, copied exactly."
    )


class ExecutiveSummaryOut(StrictModel):
    """The one page an IC member reads if they read nothing else.

    Structured rather than free prose because the prose version reliably came
    back as a single 280-word paragraph of citation-dense sentences averaging
    50 words -- technically complete and unreadable in a meeting.
    """

    investment_thesis: str = Field(
        description=(
            "2-3 sentences: what scientific proposition the investment rests on, in plain "
            "language. An IC member who has not read the deck should understand the bet. "
            "No citations in this field."
        )
    )
    key_strengths: list[str] = Field(
        description=(
            "3-5 bullets, each one line. What is genuinely established, strongest first. "
            "Cite [C#]/[E#] at the end of the bullet. Only include what the evidence "
            "supports -- if little is established, say so in fewer bullets rather than "
            "padding."
        )
    )
    key_risks: list[str] = Field(
        description=(
            "3-5 bullets, each one line, most decision-relevant first. Distinguish a "
            "contradicted claim from an unverified one in the wording. Cite [C#]/[E#]."
        )
    )
    recommendation_line: str = Field(
        description=(
            "One sentence stating the recommendation and the single reason for it. Must "
            "agree with the computed recommendation supplied; do not invent a different one."
        )
    )
    diligence_priorities: list[str] = Field(
        description=(
            "Exactly 3 items: the highest-impact things to do next, each phrased as an "
            "action with the document or dataset to request. Drawn from the ranked "
            "questions supplied."
        )
    )


class ReportOut(StrictModel):
    title: str = Field(description="Memo title including the company name where known.")
    executive_summary: ExecutiveSummaryOut = Field(
        description="The structured one-page IC summary."
    )
    sections: list[ReportSectionOut] = Field(
        description="The memo body. Follow the section plan given in the instructions exactly."
    )
    recommendation: str = Field(
        description=(
            "A scientific-diligence recommendation (not a financial one): what the "
            "investor should do next and under what conditions."
        )
    )
    limitations: list[str] = Field(
        description="Honest limitations of this analysis, including anything that could not be verified."
    )


__all__ = [
    "AdjudicationOut",
    "BatchAdjudicationItem",
    "BatchAdjudicationOut",
    "ClaimExtractionOut",
    "ClaimVerdictOut",
    "CompanyProfileOut",
    "DataPoint",
    "DiligenceQuestionOut",
    "DimensionCommentaryOut",
    "EntityExtractionOut",
    "EvidenceComparison",
    "ExecutiveSummaryOut",
    "ExtractedClaim",
    "ExtractedEntity",
    "ExtractedTable",
    "LiteratureQuery",
    "ModalityPrecedent",
    "PageUnderstandingOut",
    "PipelineProgram",
    "QuantitativeDetail",
    "QueryPlanOut",
    "ReportOut",
    "ReportSectionOut",
    "RiskOut",
    "RisksAndQuestionsOut",
    "ScientificAssessmentOut",
    "ScorecardCommentaryOut",
    "TeamMember",
    "VisualElement",
]
