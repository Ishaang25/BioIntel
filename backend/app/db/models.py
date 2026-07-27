"""Persistence model for BioIntel.

Design notes
------------
* Everything produced by an analysis is attached to an ``AnalysisRun`` so a
  document can be re-analysed with a newer model or prompt version without
  destroying prior results (auditability matters for investment memos).
* ``EvidenceItem`` is intentionally *not* run-scoped: an external publication
  is a global fact and is deduplicated by ``(source, external_id)``.  The
  run-specific interpretation lives in ``ClaimEvidenceLink``.
* Provenance is first class: claims store the verbatim quote, page number and
  the outcome of verifying that quote against the extracted page text.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    ClaimCategory,
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
from app.core.ids import new_id
from app.db.base import Base, EnumType, JSONType, StringList, UTCDateTime, utcnow

ID = String(40)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# =========================================================== documents ===
class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("document"))
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    page_count: Mapped[int] = mapped_column(Integer, default=0)
    is_parsed: Mapped[bool] = mapped_column(Boolean, default=False)
    #: True when a meaningful share of pages carried no extractable text layer.
    requires_ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    pdf_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    #: Free-form context supplied by the analyst at upload time.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentPage.page_number",
    )
    runs: Mapped[list[AnalysisRun]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_documents_created_at", "created_at"),)


class DocumentPage(Base, TimestampMixin):
    __tablename__ = "document_pages"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("page"))
    document_id: Mapped[str] = mapped_column(
        ID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[PageKind] = mapped_column(EnumType(PageKind, 32), default=PageKind.DIGITAL_TEXT)

    width: Mapped[float] = mapped_column(Float, default=0.0)
    height: Mapped[float] = mapped_column(Float, default=0.0)
    rotation: Mapped[int] = mapped_column(Integer, default=0)

    #: Concatenated text layer as extracted by the PDF engine.
    text: Mapped[str] = mapped_column(Text, default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    vector_drawing_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Fraction of the page area covered by raster images (scanned detector).
    image_area_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    #: Relative path (under storage/renders) of the rasterised page image.
    render_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: Machine-readable tables recovered by the PDF engine.
    tables: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)

    document: Mapped[Document] = relationship(back_populates="pages")
    blocks: Mapped[list[PageBlock]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="PageBlock.block_index"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_pages_document_id"),
        Index("ix_document_pages_doc_page", "document_id", "page_number"),
    )


class PageBlock(Base):
    """A positioned text block; the anchor for claim provenance."""

    __tablename__ = "page_blocks"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("block"))
    page_id: Mapped[str] = mapped_column(
        ID, ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(ID, nullable=False, index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), default="text")
    text: Mapped[str] = mapped_column(Text, default="")
    #: [x0, y0, x1, y1] in PDF points.
    bbox: Mapped[list[float]] = mapped_column(JSONType, default=list)
    font_size: Mapped[float] = mapped_column(Float, default=0.0)
    is_bold: Mapped[bool] = mapped_column(Boolean, default=False)

    page: Mapped[DocumentPage] = relationship(back_populates="blocks")

    __table_args__ = (UniqueConstraint("page_id", "block_index", name="uq_page_blocks_page_id"),)


# ================================================================ runs ===
class AnalysisRun(Base, TimestampMixin):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("run"))
    document_id: Mapped[str] = mapped_column(
        ID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[RunStatus] = mapped_column(
        EnumType(RunStatus, 32), default=RunStatus.PENDING, index=True
    )
    current_stage: Mapped[PipelineStage | None] = mapped_column(
        EnumType(PipelineStage, 48), nullable=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)

    started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Snapshot of the configuration used, so results stay reproducible.
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    #: Counters: llm_calls, tokens, evidence_fetched, cache_hits...
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    pipeline_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    document: Mapped[Document] = relationship(back_populates="runs")
    stages: Mapped[list[RunStage]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunStage.sequence"
    )
    claims: Mapped[list[Claim]] = relationship(back_populates="run", cascade="all, delete-orphan")
    entities: Mapped[list[Entity]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    profile: Mapped[CompanyProfile | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )
    questions: Mapped[list[DiligenceQuestion]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    risks: Mapped[list[RiskFlag]] = relationship(back_populates="run", cascade="all, delete-orphan")
    report: Mapped[Report | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class RunStage(Base):
    __tablename__ = "run_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[PipelineStage] = mapped_column(EnumType(PipelineStage, 48), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StageStatus] = mapped_column(
        EnumType(StageStatus, 32), default=StageStatus.PENDING
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    run: Mapped[AnalysisRun] = relationship(back_populates="stages")

    __table_args__ = (UniqueConstraint("run_id", "stage", name="uq_run_stages_run_id"),)


class PageUnderstanding(Base):
    """Multimodal interpretation of a single page (charts, diagrams, scans)."""

    __tablename__ = "page_understandings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[str] = mapped_column(
        ID, ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    slide_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    #: Text recovered from the image for scanned/graphical pages.
    recovered_text: Mapped[str] = mapped_column(Text, default="")
    visual_elements: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    data_points: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    tables: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    scientific_content: Mapped[bool] = mapped_column(Boolean, default=False)
    used_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("run_id", "page_id", name="uq_page_understandings_run_id"),)


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    company_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    one_liner: Mapped[str | None] = mapped_column(Text, nullable=True)
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    headquarters: Mapped[str | None] = mapped_column(String(256), nullable=True)
    company_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lead_program: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lead_indication: Mapped[str | None] = mapped_column(String(256), nullable=True)
    modality: Mapped[str | None] = mapped_column(String(128), nullable=True)
    development_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    team: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    funding: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    partnerships: Mapped[list[str]] = mapped_column(StringList, default=list)
    ip_position: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    stated_asks: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_pages: Mapped[list[int]] = mapped_column(JSONType, default=list)

    run: Mapped[AnalysisRun] = relationship(back_populates="profile")


# ============================================================ entities ===
class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("entity"))
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[EntityType] = mapped_column(EnumType(EntityType, 32), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    #: Lower-cased, punctuation-stripped key used for deduplication.
    normalized_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    canonical_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    aliases: Mapped[list[str]] = mapped_column(StringList, default=list)
    #: External identifiers, e.g. {"hgnc": "HGNC:1097", "mesh": "D009369"}.
    identifiers: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_in_program: Mapped[str | None] = mapped_column(Text, nullable=True)
    salience: Mapped[float] = mapped_column(Float, default=0.0)
    mention_count: Mapped[int] = mapped_column(Integer, default=0)
    source_pages: Mapped[list[int]] = mapped_column(JSONType, default=list)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    run: Mapped[AnalysisRun] = relationship(back_populates="entities")

    __table_args__ = (
        UniqueConstraint("run_id", "entity_type", "normalized_key", name="uq_entities_run_id"),
    )


class ClaimEntity(Base):
    __tablename__ = "claim_entities"

    claim_id: Mapped[str] = mapped_column(
        ID, ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(
        ID, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)


# ============================================================== claims ===
class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("claim"))
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(ID, nullable=False, index=True)

    #: Normalised, self-contained restatement of the company's assertion.
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: Verbatim text from the deck that the statement is based on.
    verbatim_quote: Mapped[str] = mapped_column(Text, default="")
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    block_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: True when the quote originated from a vision reading of a chart/scan.
    from_visual: Mapped[bool] = mapped_column(Boolean, default=False)

    category: Mapped[ClaimCategory] = mapped_column(
        EnumType(ClaimCategory, 48), default=ClaimCategory.OTHER
    )
    claimed_evidence_tier: Mapped[EvidenceTier] = mapped_column(
        EnumType(EvidenceTier, 32), default=EvidenceTier.NONE_STATED
    )
    #: Structured numbers asserted by the claim (effect sizes, n, p-values...).
    quantitative: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)

    is_scientific: Mapped[bool] = mapped_column(Boolean, default=True)
    is_falsifiable: Mapped[bool] = mapped_column(Boolean, default=True)
    hedging_language: Mapped[bool] = mapped_column(Boolean, default=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    #: Would the investment thesis change if this claim were false?
    is_thesis_critical: Mapped[bool] = mapped_column(Boolean, default=False)

    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quote_verification: Mapped[QuoteVerification] = mapped_column(
        EnumType(QuoteVerification, 32), default=QuoteVerification.NOT_APPLICABLE
    )
    quote_match_score: Mapped[float] = mapped_column(Float, default=0.0)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons: Mapped[list[str]] = mapped_column(StringList, default=list)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    run: Mapped[AnalysisRun] = relationship(back_populates="claims")
    entities: Mapped[list[Entity]] = relationship(secondary="claim_entities", lazy="selectin")
    evidence_links: Mapped[list[ClaimEvidenceLink]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )
    assessment: Mapped[ClaimAssessment | None] = relationship(
        back_populates="claim", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint("importance >= 0 AND importance <= 1", name="importance_range"),
        Index("ix_claims_run_category", "run_id", "category"),
    )


# ============================================================ evidence ===
class EvidenceItem(Base, TimestampMixin):
    """A globally deduplicated external record (paper, trial, dataset)."""

    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("evidence"))
    source: Mapped[EvidenceSource] = mapped_column(EnumType(EvidenceSource, 32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    title: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    journal: Mapped[str | None] = mapped_column(String(512), nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authors: Mapped[list[str]] = mapped_column(StringList, default=list)
    publication_types: Mapped[list[str]] = mapped_column(StringList, default=list)
    #: Normalised design classification used for scoring.
    study_design: Mapped[PublicationType] = mapped_column(
        EnumType(PublicationType, 48), default=PublicationType.OTHER
    )
    mesh_terms: Mapped[list[str]] = mapped_column(StringList, default=list)
    keywords: Mapped[list[str]] = mapped_column(StringList, default=list)

    doi: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    pmcid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nct_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_preprint: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Trial-specific fields (phase, status, enrolment, sponsor, outcomes).
    trial: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_evidence_items_source"),)

    @property
    def citation_label(self) -> str:
        if self.pmid:
            return f"PMID:{self.pmid}"
        if self.nct_id:
            return self.nct_id
        if self.doi:
            return f"doi:{self.doi}"
        return f"{self.source}:{self.external_id}"


class ClaimEvidenceLink(Base):
    """Run-scoped adjudication of one evidence item against one claim."""

    __tablename__ = "claim_evidence_links"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("link"))
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(
        ID, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ID, ForeignKey("evidence_items.id", ondelete="CASCADE"), nullable=False, index=True
    )

    stance: Mapped[Stance] = mapped_column(EnumType(Stance, 32), default=Stance.NEUTRAL)
    #: 0-1: how strongly this record bears on the claim.
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    #: 0-1: topical relevance (semantic + lexical).
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    similarity: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    #: Verbatim sentence from the abstract that justifies the stance.
    supporting_quote: Mapped[str] = mapped_column(Text, default="")
    quote_verification: Mapped[QuoteVerification] = mapped_column(
        String(32), default=QuoteVerification.NOT_APPLICABLE
    )
    quote_match_score: Mapped[float] = mapped_column(Float, default=0.0)
    caveats: Mapped[list[str]] = mapped_column(StringList, default=list)
    retrieval_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjudicated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    claim: Mapped[Claim] = relationship(back_populates="evidence_links")
    evidence: Mapped[EvidenceItem] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "run_id", "claim_id", "evidence_id", name="uq_claim_evidence_links_run_id"
        ),
    )


class ClaimAssessment(Base):
    __tablename__ = "claim_assessments"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("assessment"))
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(
        ID, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    credibility_score: Mapped[float] = mapped_column(Float, default=0.0)
    credibility_band: Mapped[CredibilityBand] = mapped_column(
        EnumType(CredibilityBand, 32), default=CredibilityBand.UNSUPPORTED
    )
    #: 0-1 confidence in the assessment itself (evidence volume/consistency).
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    supporting_count: Mapped[int] = mapped_column(Integer, default=0)
    contradicting_count: Mapped[int] = mapped_column(Integer, default=0)
    neutral_count: Mapped[int] = mapped_column(Integer, default=0)
    best_supporting_evidence_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    best_contradicting_evidence_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    evidence_quality: Mapped[float] = mapped_column(Float, default=0.0)
    consistency: Mapped[float] = mapped_column(Float, default=0.0)
    novelty: Mapped[float] = mapped_column(Float, default=0.0)
    #: Short analyst-facing verdict, explicitly marked as inference.
    verdict: Mapped[str] = mapped_column(Text, default="")
    key_uncertainties: Mapped[list[str]] = mapped_column(StringList, default=list)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    claim: Mapped[Claim] = relationship(back_populates="assessment")


class RiskFlag(Base):
    __tablename__ = "risk_flags"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("risk"))
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[RiskCategory] = mapped_column(EnumType(RiskCategory, 48), nullable=False)
    severity: Mapped[RiskSeverity] = mapped_column(
        EnumType(RiskSeverity, 32), default=RiskSeverity.MEDIUM
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    basis: Mapped[str] = mapped_column(String(32), default="inferred")
    evidence_ids: Mapped[list[str]] = mapped_column(StringList, default=list)
    source_pages: Mapped[list[int]] = mapped_column(JSONType, default=list)
    #: True when generated by deterministic rules rather than the model.
    is_rule_based: Mapped[bool] = mapped_column(Boolean, default=False)

    run: Mapped[AnalysisRun] = relationship(back_populates="risks")


class DiligenceQuestion(Base):
    __tablename__ = "diligence_questions"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("question"))
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[QuestionPriority] = mapped_column(
        EnumType(QuestionPriority, 32), default=QuestionPriority.MEDIUM
    )
    category: Mapped[RiskCategory] = mapped_column(
        EnumType(RiskCategory, 48), default=RiskCategory.SCIENTIFIC
    )
    what_good_looks_like: Mapped[str] = mapped_column(Text, default="")
    related_claim_ids: Mapped[list[str]] = mapped_column(StringList, default=list)
    related_evidence_ids: Mapped[list[str]] = mapped_column(StringList, default=list)
    rank: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[AnalysisRun] = relationship(back_populates="questions")


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("report"))
    run_id: Mapped[str] = mapped_column(
        ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    title: Mapped[str] = mapped_column(String(512), default="Scientific Due Diligence Memo")
    executive_summary: Mapped[str] = mapped_column(Text, default="")
    #: Ordered list of {id, heading, basis, body_markdown, citations[...]}.
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    #: Overall scientific credibility, 0-100.
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    overall_band: Mapped[CredibilityBand] = mapped_column(
        EnumType(CredibilityBand, 32), default=CredibilityBand.UNSUPPORTED
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    markdown: Mapped[str] = mapped_column(Text, default="")
    limitations: Mapped[list[str]] = mapped_column(StringList, default=list)

    run: Mapped[AnalysisRun] = relationship(back_populates="report")


# ================================================================ jobs ===
class Job(Base, TimestampMixin):
    """Durable work queue row.

    A single table plus optimistic locking gives us at-least-once delivery,
    retries with backoff, and crash recovery without adding Redis/Celery to
    the deployment footprint.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: new_id("job"))
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        EnumType(JobStatus, 32), default=JobStatus.QUEUED, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after", "priority"),)


class LLMCallLog(Base):
    """Per-call observability + cost accounting."""

    __tablename__ = "llm_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(48), nullable=True)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="openai")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class RetrievalCache(Base):
    """HTTP-level cache for external literature APIs (polite + fast)."""

    __tablename__ = "retrieval_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    request_url: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, index=True)


__all__ = [
    "AnalysisRun",
    "Claim",
    "ClaimAssessment",
    "ClaimEntity",
    "ClaimEvidenceLink",
    "CompanyProfile",
    "DiligenceQuestion",
    "Document",
    "DocumentPage",
    "Entity",
    "EvidenceItem",
    "Job",
    "LLMCallLog",
    "PageBlock",
    "PageUnderstanding",
    "Report",
    "RetrievalCache",
    "RiskFlag",
    "RunStage",
]
