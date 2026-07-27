"""Shared state for a single pipeline execution."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from app.analysis.adjudicator import ClaimAdjudication
from app.analysis.corroboration import CorroborationAssessment
from app.analysis.questions import RiskQuestionResult
from app.analysis.rules import ClaimContext
from app.analysis.scorecard import Scorecard
from app.analysis.scoring import ClaimScore, ClaimScoringInput, OverallScore
from app.analysis.verification import VerificationResult
from app.core.enums import PipelineStage
from app.evidence.retriever import RetrievalResult
from app.extraction.claims import ClaimExtractionResult
from app.extraction.entities import EntityExtractionResult
from app.extraction.page_understanding import PageInput, PageResult
from app.llm.schemas import ClaimVerdictOut, CompanyProfileOut
from app.reporting.builder import BuiltReport, ReferenceTable


@dataclass(slots=True)
class RunContext:
    """Everything one analysis produces, accumulated as stages complete.

    Kept out of the ORM deliberately: stages persist their own results, and
    this object carries the in-memory representation between them so that no
    stage has to re-query and re-hydrate the previous stage's rows.
    """

    run_id: str
    document_id: str
    started_at: dt.datetime

    # -- parse -------------------------------------------------------------
    pages: list[PageInput] = field(default_factory=list)
    page_count: int = 0
    requires_ocr: bool = False

    # -- understanding -----------------------------------------------------
    page_results: dict[str, PageResult] = field(default_factory=dict)
    #: page_number -> composite text used by every downstream stage
    composite_pages: list[dict[str, Any]] = field(default_factory=list)

    # -- extraction --------------------------------------------------------
    profile: CompanyProfileOut | None = None
    entities: EntityExtractionResult | None = None
    claims: ClaimExtractionResult | None = None
    #: claim_id -> persisted claim id
    claim_ids: dict[int, str] = field(default_factory=dict)

    # -- evidence ----------------------------------------------------------
    retrieval_results: list[RetrievalResult] = field(default_factory=list)
    adjudications: dict[str, ClaimAdjudication] = field(default_factory=dict)
    #: Persisted evidence item id keyed by dedupe key.
    evidence_ids: dict[str, str] = field(default_factory=dict)

    # -- assessment --------------------------------------------------------
    #: Authoritative verification outcomes, keyed by claim id.
    verifications: dict[str, VerificationResult] = field(default_factory=dict)
    #: What the evidence establishes about each claim, before scoring.
    corroborations: dict[str, CorroborationAssessment] = field(default_factory=dict)
    claim_scores: dict[str, ClaimScore] = field(default_factory=dict)
    claim_inputs: dict[str, ClaimScoringInput] = field(default_factory=dict)
    verdicts: dict[str, ClaimVerdictOut] = field(default_factory=dict)
    claim_contexts: list[ClaimContext] = field(default_factory=list)
    overall: OverallScore | None = None
    #: The multi-dimensional IC scorecard.
    scorecard: Scorecard | None = None
    #: VC-style scientific reasoning over the thesis.
    scientific_assessment: Any | None = None

    # -- questions & report ------------------------------------------------
    risks_questions: RiskQuestionResult | None = None
    references: ReferenceTable = field(default_factory=ReferenceTable)
    report: BuiltReport | None = None

    # -- bookkeeping -------------------------------------------------------
    stage_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    degraded: bool = False

    def record(self, stage: PipelineStage, metrics: dict[str, Any]) -> None:
        self.stage_metrics[stage.value] = metrics

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def pages_with_content(self) -> int:
        return sum(1 for p in self.composite_pages if (p.get("text") or "").strip())
