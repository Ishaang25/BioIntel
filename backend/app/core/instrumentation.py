"""Production instrumentation and metrics collection.

Captures runtime, token usage, cost, and workflow metrics for every pipeline run.
Produces structured JSON output and human-readable summaries.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class StageMetrics:
    """Per-stage execution metrics."""

    stage: str
    status: str
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    llm_calls: int = 0
    llm_latency_ms: int = 0
    entity_count: int = 0
    claim_count: int = 0
    evidence_records: int = 0
    error: str | None = None


@dataclass(slots=True)
class RunMetrics:
    """Comprehensive metrics for one analysis run."""

    run_id: str
    document_id: str
    status: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    total_runtime_ms: int = 0

    # Pages and extraction
    pages_total: int = 0
    pages_with_content: int = 0
    requires_ocr: bool = False

    # Claims and entities
    entities_extracted: int = 0
    entities_deduped: int = 0
    claims_total: int = 0
    claims_verified: int = 0
    claims_scored: int = 0

    # Evidence
    evidence_retrieved: int = 0
    evidence_ranked: int = 0
    unique_sources: int = 0  # PubMed, ClinicalTrials, etc.

    # Verification and scoring
    claims_corroborated: int = 0
    claims_contradicted: int = 0
    claims_unverified: int = 0
    verification_coverage: float = 0.0  # [0.0, 1.0]
    assessment_confidence: float = 0.0  # [0.0, 1.0]

    # LLM usage
    total_input_tokens: int = 0
    total_completion_tokens: int = 0
    total_cached_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_llm_calls: int = 0
    total_llm_latency_ms: int = 0

    # Cost (USD)
    estimated_input_cost: float = 0.0
    estimated_completion_cost: float = 0.0
    estimated_reasoning_cost: float = 0.0
    total_estimated_cost: float = 0.0

    # Report
    report_sections: int = 0
    report_length_chars: int = 0
    references_count: int = 0
    questions_count: int = 0

    # Degradation and warnings
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)

    # Stage breakdown
    stages: list[StageMetrics] = field(default_factory=list)

    # Provider info
    llm_provider: str | None = None
    retrieval_backend: str = "pubmed/clinicaltrials/openfda"

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        data = asdict(self)
        # Convert datetimes to ISO format
        if self.started_at:
            data["started_at"] = self.started_at.isoformat()
        if self.finished_at:
            data["finished_at"] = self.finished_at.isoformat()
        return data

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=2)

    def save_json(self, path: Path | str) -> None:
        """Write metrics to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        log.info("instrumentation.saved", path=str(path))

    def to_summary_markdown(self) -> str:
        """Generate a human-readable execution summary."""
        duration_sec = self.total_runtime_ms / 1000

        minutes, seconds = divmod(int(duration_sec), 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            duration_str = f"{hours}h {minutes:02d}m {seconds:02d}s"
        elif minutes > 0:
            duration_str = f"{minutes}m {seconds:02d}s"
        else:
            duration_str = f"{seconds}s"

        lines = [
            "# BioIntel Analysis Summary",
            "",
            f"**Status:** {self.status}",
            f"**Run ID:** {self.run_id}",
            f"**Started:** {self.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Execution Profile",
            "",
            "| Stage | Duration | Status |",
            "|-------|----------|--------|",
        ]

        for stage in self.stages:
            stage_minutes, stage_secs = divmod(int(stage.duration_ms / 1000), 60)
            stage_str = (
                f"{stage_minutes}m {stage_secs:02d}s"
                if stage_minutes > 0
                else f"{stage_secs}s"
            )
            lines.append(f"| {stage.stage} | {stage_str} | {stage.status} |")

        lines.extend([
            "",
            f"**Total Runtime:** {duration_str}",
            "",
            "## Document & Extraction",
            "",
            f"- Pages: {self.pages_total} ({self.pages_with_content} with content)",
            f"- OCR Required: {'Yes' if self.requires_ocr else 'No'}",
            f"- Entities: {self.entities_extracted} (dedup: {self.entities_deduped})",
            f"- Claims: {self.claims_total} (scored: {self.claims_scored})",
            "",
            "## Evidence & Verification",
            "",
            f"- Evidence Records Retrieved: {self.evidence_retrieved}",
            f"- Unique Sources: {self.unique_sources}",
            f"- Claims Corroborated: {self.claims_corroborated}",
            f"- Claims Contradicted: {self.claims_contradicted}",
            f"- Claims Unverified: {self.claims_unverified}",
            f"- Verification Coverage: {self.verification_coverage * 100:.1f}%",
            f"- Assessment Confidence: {self.assessment_confidence:.2f}",
            "",
            "## Model Usage",
            "",
            f"- Provider: {self.llm_provider or 'none (offline mode)'}",
            f"- LLM Calls: {self.total_llm_calls}",
            f"- Input Tokens: {self.total_input_tokens:,}",
            f"- Completion Tokens: {self.total_completion_tokens:,}",
            f"- Cached Tokens: {self.total_cached_tokens:,}",
            f"- Total Tokens: {self.total_input_tokens + self.total_completion_tokens:,}",
            f"- Total Latency: {self.total_llm_latency_ms / 1000:.1f}s",
            "",
            "## Cost (USD)",
            "",
            f"- Input: ${self.estimated_input_cost:.2f}",
            f"- Completion: ${self.estimated_completion_cost:.2f}",
            f"- Reasoning: ${self.estimated_reasoning_cost:.2f}",
            f"- **Total: ${self.total_estimated_cost:.2f}**",
            "",
            "## Report",
            "",
            f"- Sections: {self.report_sections}",
            f"- Length: {self.report_length_chars:,} characters",
            f"- References: {self.references_count}",
            f"- Questions: {self.questions_count}",
            "",
        ])

        if self.degraded:
            lines.extend([
                "## ⚠️ Degraded Mode",
                "",
                "This analysis ran without an LLM provider and used BioIntel's deterministic ",
                "offline analyzer. Chart reading, figure interpretation, and semantic evidence ",
                "adjudication were not performed.",
                "",
            ])

        if self.warnings:
            lines.extend([
                "## Warnings",
                "",
            ])
            for warning in self.warnings:
                lines.append(f"- {warning}")
            lines.append("")

        return "\n".join(lines)

    def save_summary_markdown(self, path: Path | str) -> None:
        """Write summary to a Markdown file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_summary_markdown(), encoding="utf-8")
        log.info("instrumentation.summary_saved", path=str(path))


class MetricsCollector:
    """Accumulates metrics during a run and produces output."""

    def __init__(self, run_id: str, document_id: str) -> None:
        self.run_id = run_id
        self.document_id = document_id
        self.started_at = dt.datetime.now(dt.UTC)
        self._metrics = RunMetrics(
            run_id=run_id,
            document_id=document_id,
            status="running",
            started_at=self.started_at,
        )

    def update_status(self, status: str) -> None:
        """Set the final run status."""
        self._metrics.status = status
        self._metrics.finished_at = dt.datetime.now(dt.UTC)

    def record_stage(
        self,
        stage: str,
        status: str,
        duration_ms: int,
        **kwargs: Any,
    ) -> None:
        """Record metrics for a completed stage."""
        stage_metrics = StageMetrics(
            stage=stage,
            status=status,
            duration_ms=duration_ms,
            **{k: v for k, v in kwargs.items() if k in StageMetrics.__dataclass_fields__},
        )
        self._metrics.stages.append(stage_metrics)

    @property
    def metrics(self) -> RunMetrics:
        """Access the current metrics object."""
        return self._metrics

    def finalize(self) -> RunMetrics:
        """Finalize metrics (compute totals, etc.)."""
        # Sum up stage timings
        self._metrics.total_runtime_ms = sum(s.duration_ms for s in self._metrics.stages)

        # Rollup token usage
        self._metrics.total_input_tokens = sum(s.input_tokens for s in self._metrics.stages)
        self._metrics.total_completion_tokens = sum(
            s.output_tokens for s in self._metrics.stages
        )
        self._metrics.total_cached_tokens = sum(s.cached_tokens for s in self._metrics.stages)
        self._metrics.total_reasoning_tokens = sum(s.reasoning_tokens for s in self._metrics.stages)
        self._metrics.total_llm_calls = sum(s.llm_calls for s in self._metrics.stages)
        self._metrics.total_llm_latency_ms = sum(s.llm_latency_ms for s in self._metrics.stages)

        # Rollup entities and claims
        self._metrics.entities_extracted = sum(s.entity_count for s in self._metrics.stages)
        self._metrics.claims_total = sum(s.claim_count for s in self._metrics.stages)
        self._metrics.evidence_retrieved = sum(s.evidence_records for s in self._metrics.stages)

        return self._metrics
