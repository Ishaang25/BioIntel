"""API response contracts.

Separate from the LLM schemas on purpose: these are the public surface and
change for product reasons, while the LLM schemas change for prompt-engineering
reasons.  Coupling them would make every prompt tweak a breaking API change.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    ClaimCategory,
    ClaimType,
    CorroborationStatus,
    CredibilityBand,
    EntityType,
    EvidenceSource,
    EvidenceTier,
    JobStatus,
    PageKind,
    PipelineStage,
    PublicationType,
    QuestionPriority,
    QuoteVerification,
    RiskCategory,
    RiskSeverity,
    RunStatus,
    StageStatus,
    Stance,
)

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] | None = None
    request_id: str | None = None


# ============================================================== documents ===
class DocumentOut(ApiModel):
    id: str
    filename: str
    content_hash: str
    size_bytes: int
    page_count: int
    is_parsed: bool
    requires_ocr: bool
    notes: str | None = None
    pdf_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime


class DocumentDetailOut(DocumentOut):
    latest_run_id: str | None = None
    run_count: int = 0


class PageOut(ApiModel):
    page_number: int
    kind: PageKind
    char_count: int
    word_count: int
    image_count: int
    table_count: int
    has_render: bool = False
    text: str | None = None


class UploadResponse(ApiModel):
    document: DocumentOut
    created: bool
    run: RunOut | None = None


# ==================================================================== runs ===
class StageOut(ApiModel):
    stage: PipelineStage
    sequence: int
    status: StageStatus
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class RunOut(ApiModel):
    id: str
    document_id: str
    status: RunStatus
    current_stage: PipelineStage | None = None
    progress: float
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    pipeline_version: str
    created_at: dt.datetime


class RunDetailOut(RunOut):
    document: DocumentOut
    stages: list[StageOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    profile: CompanyProfileOut | None = None
    degraded: bool = False


class CompanyProfileOut(ApiModel):
    company_name: str | None = None
    one_liner: str | None = None
    founded_year: int | None = None
    headquarters: str | None = None
    company_stage: str | None = None
    lead_program: str | None = None
    lead_indication: str | None = None
    modality: str | None = None
    development_stage: str | None = None
    pipeline: list[dict[str, Any]] = Field(default_factory=list)
    team: list[dict[str, Any]] = Field(default_factory=list)
    funding: dict[str, Any] = Field(default_factory=dict)
    partnerships: list[str] = Field(default_factory=list)
    ip_position: str | None = None
    business_model: str | None = None
    source_pages: list[int] = Field(default_factory=list)


class CreateRunRequest(BaseModel):
    document_id: str
    force: bool = Field(
        default=False,
        description="Start a new run even if another is already in progress for this document.",
    )


# ================================================================= claims ===
class QuantitativeOut(ApiModel):
    metric: str
    value: str
    unit: str | None = None
    comparator: str | None = None
    sample_size: str | None = None
    p_value: str | None = None
    model_system: str | None = None


class AssessmentOut(ApiModel):
    credibility_score: float
    credibility_band: CredibilityBand
    #: What the evidence established. Distinguishes "nothing found" from
    #: "evidence disagrees" -- these are scored completely differently.
    corroboration_status: CorroborationStatus
    corroboration_rationale: str = ""
    #: False when the claim is excluded from credibility scoring by type.
    is_scorable: bool = True
    score_explanation: str = ""
    verification_status: str | None = None
    verification_source: str | None = None
    verification_detail: str | None = None
    verification_identifiers: list[str] = Field(default_factory=list)
    confidence: float
    supporting_count: int
    contradicting_count: int
    neutral_count: int
    evidence_quality: float
    consistency: float
    novelty: float
    verdict: str
    key_uncertainties: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, Any] = Field(default_factory=dict)


class EvidenceOut(ApiModel):
    id: str
    source: EvidenceSource
    external_id: str
    title: str
    abstract: str | None = None
    journal: str | None = None
    publication_year: int | None = None
    authors: list[str] = Field(default_factory=list)
    study_design: PublicationType
    doi: str | None = None
    pmid: str | None = None
    nct_id: str | None = None
    url: str | None = None
    citation_count: int | None = None
    is_retracted: bool = False
    is_preprint: bool = False
    trial: dict[str, Any] = Field(default_factory=dict)


class EvidenceLinkOut(ApiModel):
    id: str
    claim_id: str
    stance: Stance
    strength: float
    relevance: float
    similarity: float
    rationale: str
    supporting_quote: str
    quote_verification: QuoteVerification
    caveats: list[str] = Field(default_factory=list)
    evidence: EvidenceOut


class EntityRefOut(ApiModel):
    id: str
    entity_type: EntityType
    name: str
    canonical_name: str | None = None


class ClaimOut(ApiModel):
    id: str
    statement: str
    verbatim_quote: str
    page_number: int
    from_visual: bool
    claim_type: ClaimType
    category: ClaimCategory
    claimed_evidence_tier: EvidenceTier
    quantitative: list[dict[str, Any]] = Field(default_factory=list)
    is_scientific: bool
    is_falsifiable: bool
    hedging_language: bool
    importance: float
    is_thesis_critical: bool
    extraction_confidence: float
    quote_verification: QuoteVerification
    quote_match_score: float
    needs_human_review: bool
    review_reasons: list[str] = Field(default_factory=list)
    entities: list[EntityRefOut] = Field(default_factory=list)
    assessment: AssessmentOut | None = None


class ClaimDetailOut(ClaimOut):
    evidence_links: list[EvidenceLinkOut] = Field(default_factory=list)


class EntityOut(ApiModel):
    id: str
    entity_type: EntityType
    name: str
    canonical_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    role_in_program: str | None = None
    salience: float
    mention_count: int
    source_pages: list[int] = Field(default_factory=list)
    extraction_confidence: float


# =========================================================== risk & report ===
class RiskOut(ApiModel):
    id: str
    claim_id: str | None = None
    category: RiskCategory
    severity: RiskSeverity
    title: str
    description: str
    basis: str
    evidence_ids: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    is_rule_based: bool


class QuestionOut(ApiModel):
    id: str
    question: str
    rationale: str
    priority: QuestionPriority
    category: RiskCategory
    what_good_looks_like: str
    related_claim_ids: list[str] = Field(default_factory=list)
    rank: int


class ReportOut(ApiModel):
    id: str
    run_id: str
    title: str
    executive_summary: str
    sections: list[dict[str, Any]] = Field(default_factory=list)
    overall_score: float
    overall_band: CredibilityBand
    confidence: float
    recommendation: str
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    #: Ten-dimension IC scorecard with per-dimension drivers and confidence.
    scorecard: dict[str, Any] = Field(default_factory=dict)
    scientific_assessment: dict[str, Any] = Field(default_factory=dict)
    ic_recommendation: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    markdown: str
    created_at: dt.datetime


# ================================================================= system ===
class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    llm_provider: str
    llm_degraded: bool
    retrieval_enabled: bool
    job_mode: str
    queue: dict[str, int] = Field(default_factory=dict)


class JobOut(ApiModel):
    id: str
    job_type: str
    status: JobStatus
    attempts: int
    max_attempts: int
    run_id: str | None = None
    last_error: str | None = None
    created_at: dt.datetime


RunDetailOut.model_rebuild()
UploadResponse.model_rebuild()
