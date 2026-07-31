"""The analysis pipeline.

Executes the ten stages in order, persisting each stage's output before moving
on so that a failure late in the run still leaves the analyst with everything
produced so far.  Stage failures are classified:

* **fatal** -- parse and claims: without them there is nothing to analyse;
* **degrading** -- everything else: the run continues and the missing content
  is recorded as an explicit limitation on the report.

That distinction is what makes the product usable against messy real decks:
a single unreadable page or a PubMed outage must not cost the analyst the
whole report.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.analysis.adjudicator import Adjudicator, ClaimAdjudication
from app.analysis.claim_policy import corroboration_guidance
from app.analysis.corroboration import assess_corroboration, status_distribution
from app.analysis.questions import RiskQuestionStage
from app.analysis.rules import ClaimContext
from app.analysis.rules import evaluate as evaluate_rules
from app.analysis.rules import summarise as summarise_rules
from app.analysis.scorecard import ScorecardInput, build_scorecard
from app.analysis.scoring import (
    ClaimScoringInput,
    OverallScore,
    score_claim,
    score_run,
)
from app.analysis.verification import (
    VERIFIABLE_TYPES,
    RegulatoryVerifier,
    VerificationRequest,
    extract_asserted_phase,
)
from app.analysis.verification import summarise as summarise_verification
from app.core.config import settings
from app.core.enums import (
    ADVERSE_CORROBORATION,
    CORROBORATED_STATUSES,
    STAGE_ORDER,
    STAGE_WEIGHTS,
    UNCHECKED_CORROBORATION,
    ClaimCategory,
    ClaimType,
    EvidenceTier,
    PipelineStage,
    QuoteVerification,
    RunStatus,
    StageStatus,
    Stance,
    VerificationStatus,
)
from app.core.errors import BioIntelError, PipelineError
from app.core.instrumentation import RunMetrics, StageMetrics
from app.core.logging import bind_run_context, clear_run_context, get_logger
from app.db.models import (
    AnalysisRun,
    Claim,
    ClaimAssessment,
    ClaimEntity,
    ClaimEvidenceLink,
    CompanyProfile,
    DiligenceQuestion,
    Document,
    DocumentPage,
    Entity,
    EvidenceItem,
    PageBlock,
    PageUnderstanding,
    Report,
    RiskFlag,
    RunStage,
)
from app.db.session import session_scope
from app.evidence.models import EvidenceRecord
from app.evidence.normalization import expand_query_terms
from app.evidence.retriever import EvidenceRetriever, RetrievalRequest
from app.extraction.claims import ClaimExtractionStage, VerifiedClaim
from app.extraction.entities import CompanyProfileStage, EntityExtractionStage
from app.extraction.page_understanding import (
    PageInput,
    PageUnderstandingStage,
    composite_page_text,
)
from app.ingestion.pdf_parser import parse_pdf
from app.llm import prompts
from app.llm.client import LLMClient
from app.llm.schemas import ClaimVerdictOut, QueryPlanOut, ScientificAssessmentOut
from app.pipeline.context import RunContext
from app.pipeline.progress import StageProgress
from app.reporting.builder import ReferenceTable, ReportBuilder
from app.reporting.renderer import render_markdown
from app.utils.text import normalize_entity_key, truncate

log = get_logger(__name__)

PIPELINE_VERSION = "1.0.0"

#: Stages whose failure aborts the run.
FATAL_STAGES = {PipelineStage.PARSE, PipelineStage.CLAIMS}

#: Claims we bother retrieving literature for, in importance order.
MAX_CLAIMS_FOR_RETRIEVAL = 24


class CancelledError(BioIntelError):
    code = "run_cancelled"
    status_code = 409
    message = "The analysis was cancelled."


class AnalysisPipeline:
    """Runs one :class:`~app.db.models.AnalysisRun` to completion."""

    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        retriever: EvidenceRetriever | None = None,
        verifier: RegulatoryVerifier | None = None,
        should_cancel: Any = None,
        on_progress: Any = None,
    ) -> None:
        self.llm = llm
        self.retriever = retriever
        self.verifier = verifier
        self._owns_llm = llm is None
        self._owns_retriever = retriever is None
        self._should_cancel = should_cancel or (lambda: False)
        self._on_progress = on_progress

    # ------------------------------------------------------------ entry ---
    async def run(self, run_id: str) -> RunContext:
        _, document = _load_run(run_id)
        bind_run_context(run_id=run_id, document_id=document.id)

        if self.llm is None:
            self.llm = LLMClient(run_id=run_id)
        if self.retriever is None:
            self.retriever = EvidenceRetriever(llm=self.llm)

        context = RunContext(
            run_id=run_id,
            document_id=document.id,
            started_at=dt.datetime.now(dt.UTC),
            degraded=self.llm.is_degraded,
        )
        if context.degraded:
            context.warn(
                "No language-model provider was configured; this run used BioIntel's "
                "deterministic offline analyser. Claim interpretation, figure reading and "
                "evidence adjudication were not performed by a model."
            )

        # A retry re-executes stages that already wrote rows on the previous
        # attempt. Without this, attempt 2 dies on
        # `UNIQUE constraint failed: company_profiles.run_id` -- so the retry
        # mechanism that exists to recover from a transient failure instead
        # guarantees a different one.
        _reset_run_artefacts(run_id)
        _mark_started(run_id, self.llm.provider_name)
        _seed_stages(run_id)

        try:
            for stage in STAGE_ORDER:
                self._check_cancelled()
                await self._execute(stage, context, document)
            _mark_finished(run_id, context, self.llm)
            _log_profile(context, self.llm)
            log.info("pipeline.succeeded", **_summary(context))
        except CancelledError:
            _mark_cancelled(run_id)
            log.info("pipeline.cancelled")
            raise
        except BioIntelError as exc:
            _mark_failed(run_id, exc.code, exc.message, context, self.llm)
            _log_profile(context, self.llm)
            log.error("pipeline.failed", code=exc.code, error=exc.message)
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _mark_failed(run_id, "internal_error", str(exc)[:500], context, self.llm)
            _log_profile(context, self.llm)
            log.exception("pipeline.crashed")
            raise PipelineError(f"The analysis pipeline crashed: {exc}", cause=exc) from exc
        finally:
            await self._cleanup()
            clear_run_context()

        return context

    async def _cleanup(self) -> None:
        if self._owns_retriever and self.retriever is not None:
            await self.retriever.aclose()
        if self._owns_llm and self.llm is not None:
            await self.llm.aclose()

    def _check_cancelled(self) -> None:
        if self._should_cancel():
            raise CancelledError()

    # ------------------------------------------------------- stage runner ---
    async def _execute(self, stage: PipelineStage, context: RunContext, document: Document) -> None:
        handler = getattr(self, f"_stage_{stage.value}")
        started = dt.datetime.now(dt.UTC)
        before = self._llm_snapshot(stage)
        _update_stage(context.run_id, stage, StageStatus.RUNNING, started_at=started)
        _update_run_stage(context.run_id, stage, _progress_before(stage))
        # Stages that iterate report inside their own weight band, so the bar
        # keeps moving through the long ones instead of sitting at the value it
        # started with. See app.pipeline.progress.
        context.progress = StageProgress(
            context.run_id, _update_run_stage, base=_progress_before(stage)
        )
        log.info("stage.start", stage=stage.value)

        try:
            await handler(context, document)
        except CancelledError:
            raise
        except Exception as exc:
            duration = _elapsed_ms(started)
            message = str(exc)[:1000]
            llm_delta = self._llm_delta(stage, before)
            context.record_timing(stage, duration, status="failed", llm=llm_delta)
            _update_stage(
                context.run_id,
                stage,
                StageStatus.FAILED,
                finished_at=dt.datetime.now(dt.UTC),
                duration_ms=duration,
                error=message,
                metrics={**context.stage_metrics.get(stage.value, {}), "llm": llm_delta},
            )
            if stage in FATAL_STAGES:
                log.error(
                    "stage.failed_fatal",
                    stage=stage.value,
                    duration_ms=duration,
                    error=message,
                    **llm_delta,
                )
                raise
            log.warning(
                "stage.failed_degrading",
                stage=stage.value,
                duration_ms=duration,
                error=message,
                **llm_delta,
            )
            context.warn(
                f"The '{stage.value}' stage failed ({type(exc).__name__}); the report was "
                "produced without it."
            )
            return

        duration = _elapsed_ms(started)
        llm_delta = self._llm_delta(stage, before)
        context.record_timing(stage, duration, status="succeeded", llm=llm_delta)
        _update_stage(
            context.run_id,
            stage,
            StageStatus.SUCCEEDED,
            finished_at=dt.datetime.now(dt.UTC),
            duration_ms=duration,
            metrics={**context.stage_metrics.get(stage.value, {}), "llm": llm_delta},
        )
        _update_run_stage(context.run_id, stage, _progress_after(stage))
        if self._on_progress:
            self._on_progress(stage, _progress_after(stage))
        log.info("stage.done", stage=stage.value, duration_ms=duration, **llm_delta)

    # ------------------------------------------------------- instrumentation ---
    def _llm_snapshot(self, stage: PipelineStage) -> dict[str, Any]:
        """Copy the stage's LLM counters so the stage's own cost can be diffed."""
        if self.llm is None:
            return {}
        return dict(self.llm.metrics.stage(stage.value).to_dict())

    def _llm_delta(self, stage: PipelineStage, before: dict[str, Any]) -> dict[str, Any]:
        """What this stage spent, isolated from every other stage's calls."""
        if self.llm is None:
            return {}
        after = self.llm.metrics.stage(stage.value).to_dict()
        delta = {
            key: after.get(key, 0) - before.get(key, 0)
            for key in (
                "calls",
                "failed_calls",
                "repairs",
                "salvaged",
                "truncated",
                "provider_retries",
                "latency_ms_total",
                "input_tokens",
                "output_tokens",
            )
        }
        delta["input_tokens_max"] = after.get("input_tokens_max", 0)
        delta["latency_ms_max"] = after.get("latency_ms_max", 0)
        delta["estimated_cost_usd"] = round(
            after.get("estimated_cost_usd", 0.0) - before.get("estimated_cost_usd", 0.0), 6
        )
        return delta

    # ============================================================ stages ===
    async def _stage_parse(self, context: RunContext, document: Document) -> None:
        path = Path(document.storage_path)
        if not path.exists():
            raise PipelineError("The stored document file is missing.", detail={"path": str(path)})

        render_dir = settings.renders_dir / document.id
        parsed = await asyncio.to_thread(
            parse_pdf,
            path.read_bytes(),
            render_dir=render_dir,
            render_prefix="page",
            filename=document.filename,
        )

        with session_scope() as session:
            session.execute(
                DocumentPage.__table__.delete().where(DocumentPage.document_id == document.id)
            )
            doc = session.get(Document, document.id)
            assert doc is not None
            doc.page_count = parsed.page_count
            doc.is_parsed = True
            doc.requires_ocr = parsed.requires_ocr
            doc.pdf_metadata = parsed.metadata

            for page in parsed.pages:
                row = DocumentPage(
                    document_id=document.id,
                    page_number=page.page_number,
                    kind=page.kind,
                    width=page.width,
                    height=page.height,
                    rotation=page.rotation,
                    text=page.text,
                    char_count=page.char_count,
                    word_count=page.word_count,
                    image_count=page.image_count,
                    table_count=len(page.tables),
                    vector_drawing_count=page.vector_drawing_count,
                    image_area_ratio=page.image_area_ratio,
                    render_path=f"{document.id}/{page.render_path}" if page.render_path else None,
                    tables=[t.to_dict() for t in page.tables],
                )
                session.add(row)
                session.flush()
                for block in page.blocks:
                    session.add(
                        PageBlock(
                            page_id=row.id,
                            document_id=document.id,
                            page_number=page.page_number,
                            block_index=block.block_index,
                            block_type=block.block_type,
                            text=block.text,
                            bbox=list(block.bbox),
                            font_size=block.font_size,
                            is_bold=block.is_bold,
                        )
                    )
                context.pages.append(
                    PageInput(
                        page_id=row.id,
                        page_number=page.page_number,
                        kind=page.kind,
                        text=page.text,
                        tables=[t.to_dict() for t in page.tables],
                        render_path=Path(f"{document.id}/{page.render_path}")
                        if page.render_path
                        else None,
                        image_count=page.image_count,
                        vector_drawing_count=page.vector_drawing_count,
                        needs_vision=page.needs_vision,
                    )
                )

        context.page_count = parsed.page_count
        context.requires_ocr = parsed.requires_ocr
        empty_pages = sum(1 for p in context.pages if not p.text.strip())
        if parsed.requires_ocr:
            context.warn(
                "A significant share of pages had no text layer and were read visually; "
                "transcription errors are possible."
            )
        context.record(
            PipelineStage.PARSE,
            {
                "pages": parsed.page_count,
                "characters": sum(len(p.text) for p in context.pages),
                "pages_without_text": empty_pages,
                "tables": sum(len(p.tables) for p in context.pages),
                "requires_ocr": parsed.requires_ocr,
                "pages_needing_vision": sum(1 for p in context.pages if p.needs_vision),
            },
        )

    async def _stage_page_understanding(self, context: RunContext, document: Document) -> None:
        assert self.llm is not None
        stage = PageUnderstandingStage(self.llm, progress=context.progress)
        results = await stage.run(context.pages)

        with session_scope() as session:
            for page, result in zip(context.pages, results, strict=True):
                context.page_results[page.page_id] = result
                if result.understanding is None:
                    continue
                understanding = result.understanding
                session.add(
                    PageUnderstanding(
                        run_id=context.run_id,
                        page_id=page.page_id,
                        page_number=page.page_number,
                        slide_title=truncate(understanding.slide_title or "", 500) or None,
                        summary=understanding.summary,
                        recovered_text=understanding.recovered_text,
                        visual_elements=[
                            v.model_dump(mode="json") for v in understanding.visual_elements
                        ],
                        data_points=[d.model_dump(mode="json") for d in understanding.data_points],
                        tables=[t.model_dump(mode="json") for t in understanding.tables],
                        scientific_content=understanding.contains_scientific_content,
                        used_vision=result.used_vision,
                        model=result.model,
                    )
                )

        context.composite_pages = [
            {
                "page_number": page.page_number,
                "page_id": page.page_id,
                "slide_title": (results[i].slide_title if results[i] else None),
                "text": composite_page_text(page, results[i]),
            }
            for i, page in enumerate(context.pages)
        ]

        failures = [r for r in results if r.error]
        if failures:
            context.warn(
                f"{len(failures)} page(s) could not be interpreted visually; their charts and "
                "diagrams are absent from this analysis."
            )
        low_legibility = [
            r for r in results if r.understanding is not None and r.understanding.legibility < 0.5
        ]
        if low_legibility:
            context.warn(
                f"{len(low_legibility)} page(s) were poorly legible; values read from them are "
                "lower confidence."
            )

        context.record(
            PipelineStage.PAGE_UNDERSTANDING,
            {
                "pages_processed": len(results),
                "vision_calls": sum(1 for r in results if r.used_vision),
                "failures": len(failures),
                "low_legibility_pages": len(low_legibility),
                "scientific_pages": sum(
                    1
                    for r in results
                    if r.understanding and r.understanding.contains_scientific_content
                ),
            },
        )

    async def _stage_profile(self, context: RunContext, document: Document) -> None:
        assert self.llm is not None
        profile = await CompanyProfileStage(self.llm).run(context.composite_pages)
        context.profile = profile

        with session_scope() as session:
            session.add(
                CompanyProfile(
                    run_id=context.run_id,
                    company_name=truncate(profile.company_name or "", 250) or None,
                    one_liner=profile.one_liner,
                    founded_year=profile.founded_year,
                    headquarters=truncate(profile.headquarters or "", 250) or None,
                    company_stage=truncate(profile.company_stage or "", 60) or None,
                    lead_program=truncate(profile.lead_program or "", 250) or None,
                    lead_indication=truncate(profile.lead_indication or "", 250) or None,
                    modality=truncate(profile.modality or "", 120) or None,
                    development_stage=truncate(profile.development_stage or "", 60) or None,
                    pipeline=[p.model_dump(mode="json") for p in profile.pipeline],
                    team=[t.model_dump(mode="json") for t in profile.team],
                    funding={
                        "total_raised": profile.total_raised,
                        "current_raise": profile.current_raise,
                        "use_of_funds": profile.use_of_funds,
                    },
                    partnerships=list(profile.partnerships),
                    ip_position=profile.ip_position,
                    business_model=profile.business_model,
                    stated_asks={"current_raise": profile.current_raise},
                    source_pages=list(profile.source_pages),
                )
            )

        context.record(
            PipelineStage.PROFILE,
            {
                "company_name": profile.company_name,
                "pipeline_programs": len(profile.pipeline),
                "team_members": len(profile.team),
            },
        )

    async def _stage_entities(self, context: RunContext, document: Document) -> None:
        assert self.llm is not None
        result = await EntityExtractionStage(self.llm).run(context.composite_pages)
        context.entities = result

        with session_scope() as session:
            for entity in result.entities:
                session.add(
                    Entity(
                        run_id=context.run_id,
                        entity_type=entity.entity_type,
                        name=truncate(entity.name, 500),
                        normalized_key=truncate(entity.normalized_key, 500),
                        canonical_name=truncate(entity.canonical_name or "", 500) or None,
                        aliases=entity.aliases[:20],
                        description=entity.description,
                        role_in_program=entity.role_in_program,
                        salience=entity.salience,
                        mention_count=entity.mention_count,
                        source_pages=entity.source_pages,
                        extraction_confidence=entity.confidence,
                    )
                )
        context.record(PipelineStage.ENTITIES, result.metrics())

    async def _stage_claims(self, context: RunContext, document: Document) -> None:
        assert self.llm is not None
        result = await ClaimExtractionStage(self.llm).run(context.composite_pages)
        context.claims = result

        if not result.claims:
            # Say which of the two very different things happened. Every
            # candidate's fate is in the audit, so the answer is not a guess.
            audit = result.audit.to_dict()
            log.error(
                "claims.none_survived",
                candidates=result.candidates,
                batches=result.batches,
                batch_failures=result.batch_failures,
                rejections=audit["by_reason"],
            )
            if result.candidates:
                raise PipelineError(
                    f"The model extracted {result.candidates} candidate claim(s) from this "
                    "document but none survived verification: "
                    f"{_rejection_summary(result.audit)}. This is a verification failure, "
                    "not an empty document.",
                    detail={"rejections": audit},
                )
            raise PipelineError(
                "No verifiable scientific claims could be extracted from this document. "
                "It may not be a biotech pitch deck, or its text may be unreadable.",
                detail={"rejections": audit},
            )

        entity_index = context.entities.by_name() if context.entities else {}
        unlinked: Counter[str] = Counter()

        with session_scope() as session:
            entity_rows = {
                row.normalized_key: row.id
                for row in session.execute(
                    select(Entity).where(Entity.run_id == context.run_id)
                ).scalars()
            }
            for index, verified in enumerate(result.claims):
                claim = verified.claim
                row = Claim(
                    run_id=context.run_id,
                    document_id=document.id,
                    statement=claim.statement,
                    verbatim_quote=claim.verbatim_quote,
                    page_number=claim.page_number,
                    from_visual=verified.from_visual,
                    claim_type=claim.claim_type,
                    category=claim.category,
                    claimed_evidence_tier=claim.claimed_evidence_tier,
                    quantitative=[q.model_dump(mode="json") for q in claim.quantitative],
                    is_scientific=claim.is_scientific,
                    is_falsifiable=claim.is_falsifiable,
                    hedging_language=claim.hedging_language,
                    importance=claim.importance,
                    is_thesis_critical=claim.is_thesis_critical,
                    extraction_confidence=claim.confidence,
                    quote_verification=verified.quote_verification,
                    quote_match_score=verified.quote_match_score,
                    needs_human_review=verified.needs_human_review,
                    review_reasons=verified.review_reasons,
                )
                session.add(row)
                session.flush()
                context.claim_ids[index] = row.id

                linked: set[str] = set()
                for name in claim.entity_names:
                    entity = entity_index.get(normalize_entity_key(name))
                    entity_id = (
                        entity_rows.get(entity.normalized_key) if entity is not None else None
                    )
                    if entity_id and entity_id not in linked:
                        session.add(ClaimEntity(claim_id=row.id, entity_id=entity_id))
                        linked.add(entity_id)
                    elif entity is None:
                        unlinked["no_matching_entity"] += 1
                    elif entity_id is None:
                        unlinked["entity_not_persisted"] += 1

        if unlinked:
            # A claim links to nothing when entity extraction under-recalled or
            # the two stages disagree on normalisation. Never fatal -- claims
            # stand on their own quotes -- but it degrades retrieval, so it is
            # recorded rather than swallowed.
            log.info("claims.entity_links_missing", **dict(unlinked))
        if result.dropped_unverifiable:
            context.warn(
                f"{result.dropped_unverifiable} extracted claim(s) were discarded because their "
                "supporting quote could not be located in the document."
            )
        if result.pages_not_read:
            pages = ", ".join(str(p) for p in result.pages_not_read[:20])
            context.warn(
                f"{len(result.pages_not_read)} page(s) could not be read during claim "
                f"extraction (pages {pages}); any claims they make are absent from this "
                "analysis."
            )
        context.record(
            PipelineStage.CLAIMS,
            {**result.metrics(), "entity_link_failures": dict(unlinked)},
        )

    async def _stage_retrieval(self, context: RunContext, document: Document) -> None:
        assert self.llm is not None and self.retriever is not None
        if not settings.retrieval_enabled:
            context.warn("Literature retrieval was disabled for this run.")
            context.record(PipelineStage.RETRIEVAL, {"skipped": True})
            return

        claims = self._claims_for_retrieval(context)
        company_context = _company_context(context)

        plans = await asyncio.gather(
            *(self._plan_queries(claim, company_context) for _, claim in claims),
            return_exceptions=True,
        )

        requests: list[RetrievalRequest] = []
        for (claim_id, verified), plan in zip(claims, plans, strict=True):
            if isinstance(plan, BaseException):
                log.warning("retrieval.plan_failed", claim_id=claim_id, error=str(plan)[:200])
                requests.append(
                    RetrievalRequest(
                        claim_id=claim_id,
                        claim_statement=verified.claim.statement,
                        pubmed_queries=[_fallback_query(verified)],
                    )
                )
                continue
            requests.append(
                RetrievalRequest(
                    claim_id=claim_id,
                    claim_statement=verified.claim.statement,
                    pubmed_queries=[q.query for q in plan.pubmed_queries if q.query.strip()],
                    trial_conditions=[c for c in plan.trial_conditions if c.strip()],
                    trial_interventions=[i for i in plan.trial_interventions if i.strip()],
                )
            )

        results = await self.retriever.retrieve_many(requests)
        self._persist_evidence(context, results)

        no_evidence = sum(1 for r in results if not r.records)
        if no_evidence == len(results) and results:
            context.warn(
                "No external literature was retrieved for any claim. Either the sources were "
                "unreachable or the claims are too company-specific to search."
            )
        context.retrieval_results = results
        context.record(
            PipelineStage.RETRIEVAL,
            {
                "claims_searched": len(results),
                "claims_without_evidence": no_evidence,
                "records_retained": sum(len(r.records) for r in results),
                "records_seen": sum(r.total_before_ranking for r in results),
                "by_source": dict(self.retriever.stats),
                "source_errors": sum(len(r.errors) for r in results),
            },
        )

    def _claims_for_retrieval(self, context: RunContext) -> list[tuple[str, VerifiedClaim]]:
        assert context.claims is not None
        ranked = sorted(
            enumerate(context.claims.claims),
            key=lambda pair: (pair[1].claim.is_thesis_critical, pair[1].claim.importance),
            reverse=True,
        )
        selected: list[tuple[str, VerifiedClaim]] = []
        for index, verified in ranked:
            if not verified.claim.is_falsifiable or not verified.claim.is_scientific:
                continue
            claim_id = context.claim_ids.get(index)
            if claim_id is None:
                continue
            selected.append((claim_id, verified))
            if len(selected) >= MAX_CLAIMS_FOR_RETRIEVAL:
                break
        return selected

    async def _plan_queries(self, verified: VerifiedClaim, company_context: str) -> QueryPlanOut:
        assert self.llm is not None
        claim = verified.claim
        return await self.llm.structured(
            purpose="query_plan",
            stage="retrieval",
            system=prompts.system(),
            user=prompts.render(
                "query_plan",
                claim_statement=claim.statement,
                claim_category=_value(claim.category),
                claimed_tier=_value(claim.claimed_evidence_tier),
                entity_names=", ".join(expand_query_terms(claim.entity_names, limit=10))
                or "(none identified)",
                company_context=company_context,
            ),
            schema=QueryPlanOut,
            model=settings.model_fast,
            context={
                "claim_statement": claim.statement,
                "entity_names": claim.entity_names,
            },
        )

    def _persist_evidence(self, context: RunContext, results: list[Any]) -> None:
        with session_scope() as session:
            for result in results:
                for scored in result.records:
                    record = scored.record
                    key = (record.source, record.external_id)
                    existing = session.execute(
                        select(EvidenceItem).where(
                            EvidenceItem.source == key[0],
                            EvidenceItem.external_id == key[1],
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        context.evidence_ids[record.dedupe_key] = existing.id
                        continue
                    row = _to_evidence_row(record)
                    session.add(row)
                    session.flush()
                    context.evidence_ids[record.dedupe_key] = row.id

    async def _stage_adjudication(self, context: RunContext, document: Document) -> None:
        assert self.llm is not None and context.claims is not None
        results = context.retrieval_results
        if not results:
            context.record(PipelineStage.ADJUDICATION, {"skipped": True, "reason": "no evidence"})
            return

        adjudicator = Adjudicator(self.llm)
        claim_lookup = {
            context.claim_ids[i]: verified
            for i, verified in enumerate(context.claims.claims)
            if i in context.claim_ids
        }

        async def adjudicate(result: Any) -> tuple[str, ClaimAdjudication]:
            verified = claim_lookup.get(result.claim_id)
            if verified is None or not result.records:
                return result.claim_id, ClaimAdjudication(claim_id=result.claim_id)
            claim = verified.claim
            adjudication = await adjudicator.adjudicate_claim(
                claim_id=result.claim_id,
                claim_statement=claim.statement,
                claim_quote=claim.verbatim_quote,
                claim_category=_value(claim.category),
                claim_type=_value(claim.claim_type),
                corroboration_guidance=corroboration_guidance(claim.claim_type),
                claimed_tier=_value(claim.claimed_evidence_tier),
                records=result.records,
            )
            return result.claim_id, adjudication

        gathered = await asyncio.gather(*(adjudicate(r) for r in results), return_exceptions=True)

        failures = 0
        for outcome in gathered:
            if isinstance(outcome, BaseException):
                failures += 1
                log.warning("adjudication.claim_failed", error=str(outcome)[:300])
                continue
            claim_id, adjudication = outcome
            context.adjudications[claim_id] = adjudication

        self._persist_links(context)

        stance_totals: dict[str, int] = {}
        for adjudication in context.adjudications.values():
            for evidence in adjudication.evidence:
                stance_totals[evidence.stance.value] = (
                    stance_totals.get(evidence.stance.value, 0) + 1
                )
        unverified = sum(
            1
            for a in context.adjudications.values()
            for e in a.evidence
            if e.quote_verification is QuoteVerification.NOT_FOUND and e.stance is Stance.NEUTRAL
        )
        if unverified:
            context.warn(
                f"{unverified} evidence adjudication(s) cited a quote that could not be found "
                "in the abstract and were downgraded to neutral."
            )
        context.record(
            PipelineStage.ADJUDICATION,
            {
                "claims_adjudicated": len(context.adjudications),
                "failures": failures,
                "links": sum(len(a.evidence) for a in context.adjudications.values()),
                "by_stance": stance_totals,
                "quote_verification_failures": unverified,
            },
        )

    def _persist_links(self, context: RunContext) -> None:
        with session_scope() as session:
            for claim_id, adjudication in context.adjudications.items():
                for evidence in adjudication.evidence:
                    evidence_id = context.evidence_ids.get(evidence.record.dedupe_key)
                    if evidence_id is None:
                        continue
                    session.add(
                        ClaimEvidenceLink(
                            run_id=context.run_id,
                            claim_id=claim_id,
                            evidence_id=evidence_id,
                            stance=evidence.stance,
                            strength=evidence.strength,
                            relevance=evidence.relevance,
                            similarity=evidence.similarity,
                            rationale=evidence.rationale,
                            supporting_quote=evidence.supporting_quote,
                            quote_verification=evidence.quote_verification,
                            quote_match_score=evidence.quote_match_score,
                            caveats=evidence.caveats[:8],
                            retrieval_query=truncate(evidence.retrieval_query or "", 900) or None,
                            adjudicated_by=self.llm.provider_name if self.llm else None,
                        )
                    )

    async def _stage_assessment(self, context: RunContext, document: Document) -> None:
        assert context.claims is not None and self.llm is not None

        # 1. Settle what can be settled authoritatively. Regulatory and
        #    pipeline claims are checked against the FDA and the trial
        #    registry, because the literature cannot answer them and treating
        #    a PubMed miss as a negative finding is precisely the failure this
        #    stage exists to prevent.
        await self._verify_claims(context)

        # 2. Decide what the evidence establishes, then score. Keeping these
        #    separate is what lets "found nothing" and "found disagreement"
        #    reach the score as different things.
        for index, verified in enumerate(context.claims.claims):
            claim_id = context.claim_ids.get(index)
            if claim_id is None:
                continue
            scoring_input = _to_scoring_input(claim_id, verified)
            context.claim_inputs[claim_id] = scoring_input

            assessment = assess_corroboration(
                claim_id=claim_id,
                claim_type=scoring_input.claim_type,
                adjudication=context.adjudications.get(claim_id),
                verification=context.verifications.get(claim_id),
            )
            context.corroborations[claim_id] = assessment
            context.claim_scores[claim_id] = score_claim(scoring_input, assessment)

        # Narrative verdicts only for the claims an analyst will actually read.
        verdict_targets = sorted(
            context.claim_scores.items(),
            key=lambda kv: (
                context.claim_inputs[kv[0]].is_thesis_critical,
                context.claim_inputs[kv[0]].importance,
            ),
            reverse=True,
        )[:MAX_CLAIMS_FOR_RETRIEVAL]

        verdicts = await asyncio.gather(
            *(self._claim_verdict(context, claim_id, score) for claim_id, score in verdict_targets),
            return_exceptions=True,
        )
        for (claim_id, _), verdict in zip(verdict_targets, verdicts, strict=True):
            if isinstance(verdict, BaseException):
                log.warning(
                    "assessment.verdict_failed", claim_id=claim_id, error=str(verdict)[:200]
                )
                continue
            context.verdicts[claim_id] = verdict

        self._persist_assessments(context)

        context.claim_contexts = [
            ClaimContext(
                claim_id=claim_id,
                statement=_statement_for(context, claim_id),
                page_number=_page_for(context, claim_id),
                scoring=context.claim_inputs[claim_id],
                score=score,
                adjudication=context.adjudications.get(claim_id),
                corroboration=context.corroborations.get(claim_id),
                quote_match_score=_quote_score_for(context, claim_id),
                from_visual=_from_visual(context, claim_id),
            )
            for claim_id, score in context.claim_scores.items()
        ]

        context.overall = score_run(
            list(context.claim_scores.values()),
            context.claim_inputs,
            pages_analysed=context.pages_with_content,
            pages_total=context.page_count,
            claims_needing_review=sum(1 for c in context.claims.claims if c.needs_human_review),
        )

        # 3. Build the multi-dimensional IC scorecard. A single number cannot
        #    tell a committee which risk it is taking.
        context.scorecard = build_scorecard(
            [
                ScorecardInput(
                    claim_id=claim_id,
                    statement=_statement_for(context, claim_id),
                    scoring=context.claim_inputs[claim_id],
                    score=score,
                    corroboration=context.corroborations.get(claim_id),
                )
                for claim_id, score in context.claim_scores.items()
            ],
            pipeline_size=len(context.profile.pipeline) if context.profile else 0,
            development_stage=context.profile.development_stage if context.profile else None,
            page_coverage=(
                context.pages_with_content / context.page_count if context.page_count else 1.0
            ),
            marketing_claim_ratio=_marketing_ratio(context),
        )

        context.record(
            PipelineStage.ASSESSMENT,
            {
                "claims_scored": sum(1 for s in context.claim_scores.values() if s.scored),
                "claims_excluded_by_type": sum(
                    1 for s in context.claim_scores.values() if not s.scored
                ),
                "verdicts": len(context.verdicts),
                "overall_score": context.overall.score,
                "overall_band": context.overall.band.value,
                "confidence": context.overall.confidence,
                "bands": _band_histogram(context),
                "corroboration": status_distribution(list(context.corroborations.values())),
                "verification": summarise_verification(context.verifications),
                "scorecard": {
                    "archetype": context.scorecard.archetype.value,
                    "overall": context.scorecard.overall_score,
                    "recommendation": context.scorecard.recommendation.value,
                    "dimensions": {
                        d.dimension.value: d.score
                        for d in context.scorecard.dimensions
                        if d.assessed
                    },
                },
            },
        )

    async def _scientific_assessment(self, context: RunContext) -> None:
        """Reason about the opportunity the way an investor does.

        Runs before the memo so the report prompt can build on a considered
        view of plausibility, precedent and differentiation rather than
        deriving one inline while also managing citations.
        """
        assert self.llm is not None and context.claims is not None

        thesis = [
            v
            for v in context.claims.claims
            if v.claim.is_thesis_critical or v.claim.importance >= 0.7
        ][:12]
        if not thesis:
            return

        competitive = [
            evidence
            for adjudication in context.adjudications.values()
            for evidence in adjudication.evidence
            if evidence.stance is not Stance.UNRELATED
        ][:20]

        try:
            context.scientific_assessment = await self.llm.structured(
                purpose="scientific_assessment",
                stage="report",
                system=prompts.system(),
                user=prompts.render(
                    "scientific_assessment",
                    company_context=_company_context(context),
                    thesis_claims="\n".join(
                        f"- [{_value(v.claim.claim_type)}] {v.claim.statement}" for v in thesis
                    ),
                    evidence_digest=_format_evidence_digest(context),
                    competitive_records=(
                        "\n".join(
                            f"- {e.record.short_citation()}: {truncate(e.record.title, 160)}"
                            for e in competitive
                        )
                        or "(no competitive records were retrieved)"
                    ),
                ),
                schema=ScientificAssessmentOut,
                model=settings.model_reasoning,
                context={"modality": (context.profile.modality if context.profile else None)},
            )
        except Exception as exc:
            log.warning("scientific_assessment.failed", error=str(exc)[:300])
            context.warn("The scientific reasoning pass failed; the memo was written without it.")

    async def _verify_claims(self, context: RunContext) -> None:
        """Check regulatory and pipeline claims against authoritative sources."""
        assert context.claims is not None
        if not settings.regulatory_verification_enabled:
            return

        company = context.profile.company_name if context.profile else None
        requests: list[VerificationRequest] = []

        for index, verified in enumerate(context.claims.claims):
            claim_id = context.claim_ids.get(index)
            if claim_id is None:
                continue
            claim_type = verified.claim.claim_type
            if claim_type not in VERIFIABLE_TYPES:
                continue

            # Alias expansion is what makes this work: a deck saying
            # "mRNA-1345" must reach records filed under "mRESVIA".
            names = expand_query_terms(
                [*verified.claim.entity_names, verified.claim.statement.split(".")[0]],
                limit=6,
            )
            requests.append(
                VerificationRequest(
                    claim_id=claim_id,
                    claim_type=claim_type,
                    statement=verified.claim.statement,
                    product_names=names,
                    company_name=company,
                    asserted_phase=extract_asserted_phase(verified.claim.statement),
                    indication=(context.profile.lead_indication if context.profile else None),
                )
            )

        if not requests:
            return

        verifier = self.verifier or RegulatoryVerifier()
        owns = self.verifier is None
        try:
            context.verifications = await verifier.verify_many(requests)
        except Exception as exc:
            log.warning("verification.stage_failed", error=str(exc)[:300])
            context.warn(
                "Authoritative regulatory verification could not be completed; affected "
                "claims are recorded as not independently verified."
            )
        finally:
            if owns:
                await verifier.aclose()

        decisive = sum(1 for v in context.verifications.values() if v.is_decisive)
        log.info(
            "verification.completed",
            attempted=len(requests),
            decisive=decisive,
            refuted=sum(
                1 for v in context.verifications.values() if v.status is VerificationStatus.REFUTED
            ),
        )

    async def _claim_verdict(
        self, context: RunContext, claim_id: str, score: Any
    ) -> ClaimVerdictOut:
        assert self.llm is not None
        adjudication = context.adjudications.get(claim_id)
        scoring = context.claim_inputs[claim_id]
        verification = context.verifications.get(claim_id)
        statement = _statement_for(context, claim_id)
        quote = _quote_for(context, claim_id)

        return await self.llm.structured(
            purpose="claim_verdict",
            stage="assessment",
            system=prompts.system(),
            user=prompts.render(
                "claim_verdict",
                claim_statement=statement,
                claim_quote=truncate(quote, 400),
                claim_type=_value(scoring.claim_type),
                claimed_tier=scoring.claimed_tier.value,
                claim_category=scoring.category.value,
                corroboration_guidance=corroboration_guidance(scoring.claim_type),
                verification_summary=(
                    verification.detail
                    if verification
                    else "No authoritative source was consulted for this claim."
                ),
                supporting_count=score.supporting_count,
                contradicting_count=score.contradicting_count,
                neutral_count=score.neutral_count,
                evidence_count=len(adjudication.evidence) if adjudication else 0,
                evidence_summary=_format_adjudication(adjudication),
            ),
            schema=ClaimVerdictOut,
            model=settings.model_reasoning,
            context={
                "supporting_count": score.supporting_count,
                "contradicting_count": score.contradicting_count,
                "evidence_count": len(adjudication.evidence) if adjudication else 0,
                "claimed_evidence_tier": scoring.claimed_tier.value,
                "claim_type": _value(scoring.claim_type),
                "null_status": (
                    context.corroborations[claim_id].status.value
                    if claim_id in context.corroborations
                    else "insufficient_evidence"
                ),
            },
        )

    def _persist_assessments(self, context: RunContext) -> None:
        with session_scope() as session:
            for claim_id, score in context.claim_scores.items():
                adjudication = context.adjudications.get(claim_id)
                verdict = context.verdicts.get(claim_id)
                best_support = adjudication.best(Stance.SUPPORTS) if adjudication else None
                best_contra = adjudication.best(Stance.CONTRADICTS) if adjudication else None
                corroboration = context.corroborations.get(claim_id)
                verification = context.verifications.get(claim_id)
                session.add(
                    ClaimAssessment(
                        run_id=context.run_id,
                        claim_id=claim_id,
                        credibility_score=score.credibility_score,
                        credibility_band=score.band,
                        corroboration_status=score.corroboration,
                        corroboration_rationale=(corroboration.rationale if corroboration else ""),
                        is_scorable=score.scored,
                        score_explanation=score.explanation,
                        verification_status=(verification.status.value if verification else None),
                        verification_source=verification.source if verification else None,
                        verification_detail=verification.detail if verification else None,
                        verification_identifiers=(
                            verification.identifiers[:10] if verification else []
                        ),
                        confidence=score.confidence,
                        supporting_count=score.supporting_count,
                        contradicting_count=score.contradicting_count,
                        neutral_count=score.neutral_count,
                        best_supporting_evidence_id=(
                            context.evidence_ids.get(best_support.record.dedupe_key)
                            if best_support
                            else None
                        ),
                        best_contradicting_evidence_id=(
                            context.evidence_ids.get(best_contra.record.dedupe_key)
                            if best_contra
                            else None
                        ),
                        evidence_quality=score.evidence_quality,
                        consistency=score.consistency,
                        novelty=verdict.novelty if verdict else 0.0,
                        verdict=verdict.verdict if verdict else "",
                        key_uncertainties=(verdict.key_uncertainties if verdict else [])[:6],
                        score_breakdown={
                            **score.breakdown,
                            "translational_gap": verdict.translational_gap if verdict else None,
                        },
                    )
                )

    async def _stage_questions(self, context: RunContext, document: Document) -> None:
        assert self.llm is not None
        findings = evaluate_rules(context.claim_contexts)
        summaries, ref_to_claim = _claim_summaries(context)

        result = await RiskQuestionStage(self.llm).run(
            company_context=_company_context(context),
            claim_summaries=summaries,
            rule_findings=findings,
            ref_to_claim_id=ref_to_claim,
        )
        context.risks_questions = result

        with session_scope() as session:
            for risk in result.risks:
                session.add(
                    RiskFlag(
                        run_id=context.run_id,
                        claim_id=risk.claim_ids[0] if risk.claim_ids else None,
                        category=risk.category,
                        severity=risk.severity,
                        title=truncate(risk.title, 500),
                        description=risk.description,
                        basis="rule" if risk.is_rule_based else "inferred",
                        evidence_ids=risk.evidence_ids[:10],
                        source_pages=risk.source_pages,
                        is_rule_based=risk.is_rule_based,
                    )
                )
            for question in result.questions:
                session.add(
                    DiligenceQuestion(
                        run_id=context.run_id,
                        question=question.question,
                        rationale=question.rationale,
                        priority=question.priority,
                        category=question.category,
                        what_good_looks_like=question.what_good_looks_like,
                        related_claim_ids=question.claim_ids[:8],
                        rank=question.rank,
                    )
                )

        context.record(
            PipelineStage.QUESTIONS,
            {**result.metrics(), "rule_findings": summarise_rules(findings)},
        )

    async def _stage_report(self, context: RunContext, document: Document) -> None:
        assert self.llm is not None
        # Five sub-steps. The stage is the last 5% of the run and its central
        # call generates ~15k tokens, so without these the bar sits at 0.95 for
        # the whole of it and the run looks hung. Step 3 is the long one; the
        # tick before it is what tells a reader the memo is being written
        # rather than that something has stopped.
        steps = 5
        context.progress.advance(PipelineStage.REPORT, 0, steps)

        await self._scientific_assessment(context)
        context.progress.advance(PipelineStage.REPORT, 1, steps)

        summaries, _ = _claim_summaries(context)
        references = _build_references(context, summaries)
        context.references = references
        context.progress.advance(PipelineStage.REPORT, 2, steps)

        overall = context.overall or OverallScore(
            score=0.0, band=_default_band(), confidence=0.0, breakdown={}
        )
        risks = context.risks_questions.risks if context.risks_questions else []
        questions = context.risks_questions.questions if context.risks_questions else []

        report = await ReportBuilder(self.llm).build(
            company_context=_company_context(context),
            company_name=context.profile.company_name if context.profile else None,
            overall=overall,
            claim_summaries=summaries,
            risks=risks,
            questions=questions,
            references=references,
            extra_limitations=context.warnings,
            scorecard=context.scorecard,
            scientific_assessment=context.scientific_assessment,
        )
        context.report = report
        context.progress.advance(PipelineStage.REPORT, 3, steps)

        markdown = render_markdown(
            report,
            company_name=context.profile.company_name if context.profile else None,
            document_name=document.filename,
            degraded=context.degraded,
            scorecard=(context.scorecard.to_dict() if context.scorecard else None),
        )

        with session_scope() as session:
            session.add(
                Report(
                    run_id=context.run_id,
                    title=truncate(report.title, 500),
                    executive_summary=report.executive_summary,
                    sections=report.sections,
                    overall_score=report.overall_score,
                    overall_band=report.overall_band,
                    confidence=report.confidence,
                    recommendation=report.recommendation,
                    score_breakdown=report.score_breakdown,
                    scorecard=(context.scorecard.to_dict() if context.scorecard else {}),
                    scientific_assessment=(
                        context.scientific_assessment.model_dump(mode="json")
                        if context.scientific_assessment is not None
                        else {}
                    ),
                    ic_recommendation=(
                        context.scorecard.recommendation.value if context.scorecard else None
                    ),
                    citations=report.citations,
                    markdown=markdown,
                    limitations=report.limitations,
                )
            )

        context.progress.advance(PipelineStage.REPORT, steps, steps)

        context.record(
            PipelineStage.REPORT,
            {
                "sections": len(report.sections),
                "citations": len(report.citations),
                "invalid_citations": len(report.invalid_citations),
                "limitations": len(report.limitations),
                "markdown_chars": len(markdown),
            },
        )


# ================================================================ helpers ===
def _value(enum_or_str: Any) -> str:
    return enum_or_str.value if hasattr(enum_or_str, "value") else str(enum_or_str)


def _rejection_summary(audit: Any, limit: int = 3) -> str:
    """Human-readable "why nothing survived", for the error a user sees."""
    top = audit.reasons.most_common(limit)
    if not top:
        return "no reason was recorded"
    return ", ".join(f"{count} x {reason.replace('_', ' ')}" for reason, count in top)


def _fallback_query(verified: VerifiedClaim) -> str:
    """Keyword query used when the model's query planner fails for a claim."""
    from app.extraction import lexicon

    claim = verified.claim
    terms = [
        *(h.canonical for h in lexicon.find_targets(claim.statement)),
        *(h.canonical for h in lexicon.find_diseases(claim.statement)),
        *claim.entity_names,
    ]
    seen: list[str] = []
    for term in terms:
        if term and term.lower() not in {t.lower() for t in seen}:
            seen.append(term)
    if seen:
        return " AND ".join(f'"{term}"' for term in seen[:3])
    # Last resort: the longest content words from the statement itself.
    words = sorted(
        {w.strip(".,;:()") for w in claim.statement.split() if len(w) > 5},
        key=len,
        reverse=True,
    )
    return " AND ".join(words[:4]) if words else claim.statement[:120]


def _default_band():
    from app.core.enums import CredibilityBand

    return CredibilityBand.UNSUPPORTED


def _to_evidence_row(record: EvidenceRecord) -> EvidenceItem:
    return EvidenceItem(
        source=record.source,
        external_id=record.external_id,
        title=truncate(record.title, 4000),
        abstract=record.abstract,
        journal=truncate(record.journal or "", 500) or None,
        publication_year=record.publication_year,
        publication_date=record.publication_date,
        authors=record.authors[:30],
        publication_types=record.publication_types[:15],
        study_design=record.study_design,
        mesh_terms=record.mesh_terms[:40],
        keywords=record.keywords[:30],
        doi=truncate(record.doi or "", 250) or None,
        pmid=record.pmid,
        pmcid=record.pmcid,
        nct_id=record.nct_id,
        url=truncate(record.url or "", 1000) or None,
        citation_count=record.citation_count,
        is_retracted=record.is_retracted,
        is_preprint=record.is_preprint,
        trial=record.trial,
        raw=record.raw,
    )


def _to_scoring_input(claim_id: str, verified: VerifiedClaim) -> ClaimScoringInput:
    claim = verified.claim
    quantities = claim.quantitative
    return ClaimScoringInput(
        claim_id=claim_id,
        claim_type=(
            claim.claim_type
            if isinstance(claim.claim_type, ClaimType)
            else ClaimType(str(claim.claim_type))
        ),
        category=(
            claim.category if isinstance(claim.category, ClaimCategory) else ClaimCategory.OTHER
        ),
        claimed_tier=claim.claimed_evidence_tier
        if isinstance(claim.claimed_evidence_tier, EvidenceTier)
        else EvidenceTier.NONE_STATED,
        importance=claim.importance,
        is_thesis_critical=claim.is_thesis_critical,
        hedging_language=claim.hedging_language,
        quote_verification=verified.quote_verification,
        extraction_confidence=claim.confidence,
        has_sample_size=any(q.sample_size for q in quantities),
        has_statistics=any(q.p_value for q in quantities),
        has_comparator=any(q.comparator for q in quantities),
        has_effect_size=bool(quantities),
    )


def _company_context(context: RunContext) -> str:
    profile = context.profile
    if profile is None:
        return "(no company profile could be extracted)"
    parts = [
        f"Company: {profile.company_name or 'not stated'}",
        f"Description: {profile.one_liner or 'not stated'}",
        f"Lead program: {profile.lead_program or 'not stated'}",
        f"Lead indication: {profile.lead_indication or 'not stated'}",
        f"Modality: {profile.modality or 'not stated'}",
        f"Development stage: {profile.development_stage or 'not stated'}",
    ]
    if profile.pipeline:
        programs = "; ".join(
            f"{p.name} ({p.indication or 'indication not stated'}, {p.stage or 'stage not stated'})"
            for p in profile.pipeline[:6]
        )
        parts.append(f"Pipeline: {programs}")
    if context.entities:
        top = ", ".join(f"{e.name} [{e.entity_type.value}]" for e in context.entities.entities[:12])
        parts.append(f"Key entities: {top}")
    return "\n".join(parts)


def _claim_summaries(context: RunContext) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Build the ``[C#]``-keyed claim table shared by prompts and the report."""
    assert context.claims is not None
    summaries: list[dict[str, Any]] = []
    ref_to_claim: dict[str, str] = {}
    evidence_ref_counter = 1

    ordered = sorted(
        (
            (context.claim_ids[i], verified)
            for i, verified in enumerate(context.claims.claims)
            if i in context.claim_ids
        ),
        key=lambda pair: (
            pair[1].claim.is_thesis_critical,
            pair[1].claim.importance,
        ),
        reverse=True,
    )

    for index, (claim_id, verified) in enumerate(ordered, start=1):
        ref = f"C{index}"
        ref_to_claim[ref] = claim_id
        score = context.claim_scores.get(claim_id)
        verdict = context.verdicts.get(claim_id)
        adjudication = context.adjudications.get(claim_id)

        top_evidence: list[dict[str, Any]] = []
        if adjudication:
            for evidence in adjudication.evidence[:5]:
                if not evidence.is_decision_relevant and evidence.stance is not Stance.NEUTRAL:
                    continue
                top_evidence.append(
                    {
                        "ref": f"E{evidence_ref_counter}",
                        "stance": evidence.stance.value,
                        "strength": evidence.strength,
                        "relevance": evidence.relevance,
                        "citation": evidence.record.short_citation(),
                        "title": evidence.record.title,
                        "url": evidence.record.url,
                        "quote": evidence.supporting_quote,
                        "rationale": evidence.rationale,
                        "citation_label": evidence.record.citation_label,
                        "journal": evidence.record.journal,
                        "year": evidence.record.publication_year,
                    }
                )
                evidence_ref_counter += 1

        summaries.append(
            {
                "ref": ref,
                "claim_id": claim_id,
                "statement": verified.claim.statement,
                "quote": verified.claim.verbatim_quote,
                "page_number": verified.claim.page_number,
                "claim_type": _value(verified.claim.claim_type),
                "category": _value(verified.claim.category),
                "corroboration_status": (
                    context.corroborations[claim_id].status.value
                    if claim_id in context.corroborations
                    else None
                ),
                "corroboration_rationale": (
                    context.corroborations[claim_id].rationale
                    if claim_id in context.corroborations
                    else ""
                ),
                "claimed_tier": _value(verified.claim.claimed_evidence_tier),
                "is_thesis_critical": verified.claim.is_thesis_critical,
                "importance": verified.claim.importance,
                "credibility_score": round(score.credibility_score, 1) if score else None,
                "band": score.band.value if score else None,
                "confidence": round(score.confidence, 2) if score else None,
                # The evidence state travels with the claim into the memo, so
                # the report writer can tell the reader whether a claim is
                # unverified or disputed -- two things a bare score conflates.
                "evidence_state": score.evidence_state.value if score else None,
                "evidence_level": (
                    score.evidence.evidence_level.value
                    if score and score.evidence and score.evidence.evidence_level
                    else None
                ),
                "retrieval_status": (
                    score.evidence.retrieval_status.value if score and score.evidence else None
                ),
                "requires_audit": bool(score and score.requires_audit),
                "contradicted": bool(score and score.evidence and score.evidence.contradicted),
                "evidence_note": (score.evidence.note if score and score.evidence else ""),
                "supporting_count": score.supporting_count if score else 0,
                "contradicting_count": score.contradicting_count if score else 0,
                "neutral_count": score.neutral_count if score else 0,
                "verdict": verdict.verdict if verdict else "",
                "key_uncertainties": verdict.key_uncertainties if verdict else [],
                "top_evidence": top_evidence,
            }
        )
    return summaries, ref_to_claim


def _build_references(context: RunContext, summaries: list[dict[str, Any]]) -> ReferenceTable:
    table = ReferenceTable()
    for summary in summaries:
        table.claims[summary["ref"]] = {
            "kind": "claim",
            "claim_id": summary["claim_id"],
            "statement": summary["statement"],
            "quote": summary["quote"],
            "page_number": summary["page_number"],
        }
        for evidence in summary["top_evidence"]:
            table.evidence[evidence["ref"]] = {
                "kind": "evidence",
                "title": evidence["title"],
                "citation_label": evidence["citation_label"],
                "citation": evidence["citation"],
                "url": evidence["url"],
                "journal": evidence["journal"],
                "year": evidence["year"],
                "stance": evidence["stance"],
            }
    return table


def _format_adjudication(adjudication: ClaimAdjudication | None) -> str:
    if adjudication is None or not adjudication.evidence:
        return "(no external evidence was retrieved for this claim)"
    lines: list[str] = []
    for index, evidence in enumerate(adjudication.evidence[:8], start=1):
        lines.append(
            f"[E{index}] ({evidence.stance.value}, relevance {evidence.relevance:.2f}, "
            f"strength {evidence.strength:.2f}) {evidence.record.short_citation()}"
        )
        if evidence.supporting_quote:
            lines.append(f'      "{truncate(evidence.supporting_quote, 260)}"')
        if evidence.caveats:
            lines.append(f"      caveats: {'; '.join(evidence.caveats[:3])}")
    return "\n".join(lines)


def _statement_for(context: RunContext, claim_id: str) -> str:
    assert context.claims is not None
    for index, verified in enumerate(context.claims.claims):
        if context.claim_ids.get(index) == claim_id:
            return verified.claim.statement
    return ""


def _quote_for(context: RunContext, claim_id: str) -> str:
    assert context.claims is not None
    for index, verified in enumerate(context.claims.claims):
        if context.claim_ids.get(index) == claim_id:
            return verified.claim.verbatim_quote
    return ""


def _page_for(context: RunContext, claim_id: str) -> int:
    assert context.claims is not None
    for index, verified in enumerate(context.claims.claims):
        if context.claim_ids.get(index) == claim_id:
            return verified.claim.page_number
    return 0


def _quote_score_for(context: RunContext, claim_id: str) -> float:
    assert context.claims is not None
    for index, verified in enumerate(context.claims.claims):
        if context.claim_ids.get(index) == claim_id:
            return verified.quote_match_score
    return 1.0


def _from_visual(context: RunContext, claim_id: str) -> bool:
    assert context.claims is not None
    for index, verified in enumerate(context.claims.claims):
        if context.claim_ids.get(index) == claim_id:
            return verified.from_visual
    return False


def _band_histogram(context: RunContext) -> dict[str, int]:
    histogram: dict[str, int] = {}
    for score in context.claim_scores.values():
        histogram[score.band.value] = histogram.get(score.band.value, 0) + 1
    return histogram


def _summary(context: RunContext) -> dict[str, Any]:
    return {
        "pages": context.page_count,
        "claims": len(context.claim_ids),
        "evidence": len(context.evidence_ids),
        "score": context.overall.score if context.overall else None,
        "degraded": context.degraded,
    }


# ------------------------------------------------------------ persistence ---
def _load_run(run_id: str) -> tuple[AnalysisRun, Document]:
    with session_scope() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            raise PipelineError(f"Analysis run {run_id} does not exist.")
        document = session.get(Document, run.document_id)
        if document is None:
            raise PipelineError(f"Document {run.document_id} does not exist.")
        session.expunge(run)
        session.expunge(document)
        return run, document


def _mark_started(run_id: str, provider: str) -> None:
    with session_scope() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return
        run.status = RunStatus.RUNNING
        run.started_at = dt.datetime.now(dt.UTC)
        run.progress = 0.0
        run.error_code = None
        run.error_message = None
        run.pipeline_version = PIPELINE_VERSION
        run.config = {
            "pipeline_version": PIPELINE_VERSION,
            "prompt_version": prompts.PROMPT_VERSION,
            "llm_provider": provider,
            "models": {
                "reasoning": settings.model_reasoning,
                "fast": settings.model_fast,
                "vision": settings.model_vision,
                "embedding": settings.model_embedding,
            },
            "retrieval_enabled": settings.retrieval_enabled,
            "evidence_per_claim": settings.evidence_per_claim,
        }


#: Everything a run writes that is keyed by ``run_id``, ordered so that a
#: child is always deleted before its parent.
_RUN_ARTEFACT_TABLES = (
    ClaimEvidenceLink,
    ClaimAssessment,
    Claim,
    Entity,
    CompanyProfile,
    PageUnderstanding,
    DiligenceQuestion,
    RiskFlag,
    Report,
    RunStage,
)


def _reset_run_artefacts(run_id: str) -> None:
    """Delete anything a previous attempt of this run wrote.

    Reruns must be idempotent. Stages persist as they go -- deliberately, so a
    late failure still leaves the analyst everything produced so far -- which
    means a second attempt starts against a database that already holds the
    first attempt's rows.
    """
    deleted: dict[str, int] = {}
    with session_scope() as session:
        # ClaimEntity is a join table with no run_id of its own; it has to go
        # via the claims it belongs to, before those claims are deleted.
        claim_ids = select(Claim.id).where(Claim.run_id == run_id)
        session.execute(ClaimEntity.__table__.delete().where(ClaimEntity.claim_id.in_(claim_ids)))
        for model in _RUN_ARTEFACT_TABLES:
            result = session.execute(model.__table__.delete().where(model.run_id == run_id))
            if result.rowcount:
                deleted[model.__tablename__] = int(result.rowcount)
    if deleted:
        log.info("run.previous_attempt_cleared", run_id=run_id, **deleted)


def _seed_stages(run_id: str) -> None:
    with session_scope() as session:
        existing = {
            row.stage
            for row in session.execute(select(RunStage).where(RunStage.run_id == run_id)).scalars()
        }
        for sequence, stage in enumerate(STAGE_ORDER):
            if stage in existing:
                continue
            session.add(
                RunStage(run_id=run_id, stage=stage, sequence=sequence, status=StageStatus.PENDING)
            )


def _update_stage(
    run_id: str,
    stage: PipelineStage,
    status: StageStatus,
    *,
    started_at: dt.datetime | None = None,
    finished_at: dt.datetime | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    with session_scope() as session:
        row = session.execute(
            select(RunStage).where(RunStage.run_id == run_id, RunStage.stage == stage)
        ).scalar_one_or_none()
        if row is None:
            return
        row.status = status
        if started_at is not None:
            row.started_at = started_at
        if finished_at is not None:
            row.finished_at = finished_at
        if duration_ms is not None:
            row.duration_ms = duration_ms
        if error is not None:
            row.error_message = error
        if metrics is not None:
            row.metrics = metrics


def _update_run_stage(run_id: str, stage: PipelineStage, progress: float) -> None:
    with session_scope() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return
        run.current_stage = stage
        run.progress = round(min(1.0, max(run.progress, progress)), 4)


def _mark_finished(run_id: str, context: RunContext, llm: LLMClient) -> None:
    with session_scope() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return
        run.status = RunStatus.SUCCEEDED
        run.progress = 1.0
        run.current_stage = PipelineStage.REPORT
        run.finished_at = dt.datetime.now(dt.UTC)
        run.duration_ms = _elapsed_ms(context.started_at)
        run.metrics = _run_metrics(context, llm)

    # Save detailed metrics to filesystem
    _save_run_metrics(run_id, context, llm, "succeeded")


def _mark_failed(
    run_id: str, code: str, message: str, context: RunContext, llm: LLMClient | None
) -> None:
    with session_scope() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return
        run.status = RunStatus.FAILED
        run.finished_at = dt.datetime.now(dt.UTC)
        run.duration_ms = _elapsed_ms(context.started_at)
        run.error_code = code[:64]
        run.error_message = message
        if llm is not None:
            run.metrics = _run_metrics(context, llm)

    # Save detailed metrics to filesystem
    if llm is not None:
        _save_run_metrics(run_id, context, llm, "failed")


def _mark_cancelled(run_id: str) -> None:
    with session_scope() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return
        run.status = RunStatus.CANCELLED
        run.finished_at = dt.datetime.now(dt.UTC)


def _log_profile(context: RunContext, llm: LLMClient | None) -> None:
    """Emit the per-stage timing profile as one structured line.

    Deliberately a single record: a profile split across ten log lines is a
    profile nobody reads. ``slowest_stage`` is what a regression alert keys on.
    """
    timings = sorted(context.stage_timings, key=lambda t: t["duration_ms"], reverse=True)
    log.info(
        "pipeline.profile",
        total_ms=context.total_duration_ms,
        wall_ms=_elapsed_ms(context.started_at),
        slowest_stage=(timings[0]["stage"] if timings else None),
        slowest_stage_ms=(timings[0]["duration_ms"] if timings else 0),
        stages={t["stage"]: t["duration_ms"] for t in context.stage_timings},
        llm_calls=(llm.metrics.calls if llm else 0),
        llm_latency_ms=(llm.metrics.latency_ms if llm else 0),
        llm_truncated=(llm.metrics.truncated if llm else 0),
        llm_salvaged=(llm.metrics.salvaged if llm else 0),
        llm_over_input_budget=(llm.metrics.over_input_budget if llm else 0),
        max_input_tokens=(llm.metrics.max_input_tokens_seen if llm else 0),
        estimated_cost_usd=(round(llm.metrics.cost_usd, 6) if llm else 0.0),
    )


def _run_metrics(context: RunContext, llm: LLMClient) -> dict[str, Any]:
    return {
        "stages": context.stage_metrics,
        "timings": context.stage_timings,
        "llm": llm.metrics.to_dict(),
        "degraded": context.degraded,
        "warnings": context.warnings,
        "counts": {
            "pages": context.page_count,
            "claims": len(context.claim_ids),
            "entities": len(context.entities.entities) if context.entities else 0,
            "evidence": len(context.evidence_ids),
            "risks": len(context.risks_questions.risks) if context.risks_questions else 0,
            "questions": len(context.risks_questions.questions) if context.risks_questions else 0,
        },
    }


def _progress_before(stage: PipelineStage) -> float:
    total = 0.0
    for candidate in STAGE_ORDER:
        if candidate is stage:
            break
        total += STAGE_WEIGHTS.get(candidate, 0.0)
    return round(total, 4)


def _progress_after(stage: PipelineStage) -> float:
    return round(_progress_before(stage) + STAGE_WEIGHTS.get(stage, 0.0), 4)


def _elapsed_ms(since: dt.datetime) -> int:
    return int((dt.datetime.now(dt.UTC) - since).total_seconds() * 1000)


def _marketing_ratio(context: RunContext) -> float:
    """Share of extracted statements that are promotional or forward-looking.

    A signal about the deck rather than the science: it feeds disclosure
    quality and never touches credibility.
    """
    if not context.claim_inputs:
        return 0.0
    unscorable = sum(
        1
        for claim in context.claim_inputs.values()
        if claim.claim_type
        in (
            ClaimType.MARKETING,
            ClaimType.CORPORATE_VISION,
            ClaimType.STRATEGIC_OBJECTIVE,
            ClaimType.FORWARD_LOOKING,
            ClaimType.FINANCIAL_GUIDANCE,
            ClaimType.MARKET_ESTIMATE,
        )
    )
    return round(unscorable / len(context.claim_inputs), 4)


def _format_evidence_digest(context: RunContext, limit: int = 18) -> str:
    """The strongest retrieved evidence, graded, for the reasoning prompt."""
    from app.evidence.grading import grade_record

    rows: list[tuple[float, str]] = []
    for adjudication in context.adjudications.values():
        for evidence in adjudication.evidence:
            if evidence.stance is Stance.UNRELATED:
                continue
            graded = grade_record(evidence.record)
            rows.append(
                (
                    graded.weight * evidence.relevance,
                    f"- [{graded.grade.value}] ({evidence.stance.value}) "
                    f"{evidence.record.short_citation()}: "
                    f"{truncate(evidence.record.title, 150)}",
                )
            )
    rows.sort(key=lambda pair: pair[0], reverse=True)
    if not rows:
        return "(no external evidence was retrieved)"
    return "\n".join(row for _, row in rows[:limit])


def _save_run_metrics(run_id: str, context: RunContext, llm: LLMClient, status: str) -> None:
    """Save comprehensive metrics to JSON and Markdown files."""
    try:
        metrics = RunMetrics(
            run_id=run_id,
            document_id=context.document_id,
            status=status,
            started_at=context.started_at,
            finished_at=dt.datetime.now(dt.UTC),
            total_runtime_ms=context.total_duration_ms,
            pages_total=context.page_count,
            pages_with_content=context.pages_with_content,
            requires_ocr=context.requires_ocr,
            entities_extracted=len(context.entities.entities) if context.entities else 0,
            entities_deduped=context.entities.duplicates_merged if context.entities else 0,
            claims_total=len(context.claim_ids),
            claims_verified=len(context.verifications),
            claims_scored=len(context.claim_scores),
            evidence_retrieved=len(context.evidence_ids),
            claims_corroborated=sum(
                1 for c in context.corroborations.values() if c.status in CORROBORATED_STATUSES
            ),
            claims_contradicted=sum(
                1 for c in context.corroborations.values() if c.status in ADVERSE_CORROBORATION
            ),
            claims_unverified=sum(
                1 for c in context.corroborations.values() if c.status in UNCHECKED_CORROBORATION
            ),
            verification_coverage=(
                len(context.verifications) / len(context.claim_ids) if context.claim_ids else 0.0
            ),
            assessment_confidence=(context.overall.confidence if context.overall else 0.0),
            total_input_tokens=llm.metrics.usage.input_tokens,
            total_completion_tokens=llm.metrics.usage.output_tokens,
            total_cached_tokens=llm.metrics.usage.cached_input_tokens,
            total_reasoning_tokens=llm.metrics.usage.reasoning_tokens,
            total_llm_calls=llm.metrics.calls,
            total_llm_latency_ms=llm.metrics.latency_ms,
            estimated_input_cost=round(
                llm.metrics.cost.input_usd + llm.metrics.cost.cached_input_usd, 6
            ),
            estimated_completion_cost=round(llm.metrics.cost.output_usd, 6),
            estimated_reasoning_cost=round(llm.metrics.cost.reasoning_usd, 6),
            total_estimated_cost=round(llm.metrics.cost_usd, 6),
            report_sections=len(context.report.sections) if context.report else 0,
            # The rendered length is recorded by the report stage; the built
            # report holds structured sections, not the rendered document.
            report_length_chars=int(
                context.stage_metrics.get(PipelineStage.REPORT.value, {}).get("markdown_chars", 0)
            ),
            references_count=len(context.report.citations) if context.report else 0,
            questions_count=(
                len(context.risks_questions.questions) if context.risks_questions else 0
            ),
            unique_sources=len(
                {
                    scored.record.source
                    for result in context.retrieval_results
                    for scored in result.records
                }
            ),
            evidence_ranked=sum(len(result.records) for result in context.retrieval_results),
            degraded=context.degraded,
            warnings=context.warnings,
            llm_provider=llm.provider_name,
        )

        for timing in context.stage_timings:
            stage_llm = timing.get("llm", {})
            metrics.stages.append(
                StageMetrics(
                    stage=timing["stage"],
                    status=timing["status"],
                    duration_ms=timing["duration_ms"],
                    input_tokens=stage_llm.get("input_tokens", 0),
                    output_tokens=stage_llm.get("output_tokens", 0),
                    cached_tokens=stage_llm.get("cached_input_tokens", 0),
                    reasoning_tokens=stage_llm.get("reasoning_tokens", 0),
                    llm_calls=stage_llm.get("calls", 0),
                    llm_latency_ms=stage_llm.get("latency_ms_total", 0),
                )
            )

        metrics_dir = settings.metrics_dir / run_id
        metrics_dir.mkdir(parents=True, exist_ok=True)

        # Save JSON metrics
        metrics.save_json(metrics_dir / "run_metrics.json")

        # Save Markdown summary
        metrics.save_summary_markdown(metrics_dir / "run_summary.md")

        log.info("instrumentation.metrics_saved", run_id=run_id, path=str(metrics_dir))
    except Exception as exc:
        # Metrics are secondary to the analysis, so a failure here must not lose
        # a completed run. It is still a defect: log at error with the traceback
        # so it surfaces instead of decaying into a warning nobody reads. An
        # earlier version of this function referenced fields that did not exist
        # and failed silently on every run; the pipeline tests now assert the
        # artefacts are written.
        log.error(
            "instrumentation.save_failed",
            run_id=run_id,
            error=str(exc)[:300],
            exc_info=True,
        )
