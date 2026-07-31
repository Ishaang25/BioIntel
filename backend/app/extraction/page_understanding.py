"""Stage 2: multimodal page understanding.

A pitch deck's scientific substance is disproportionately in its figures: the
efficacy chart, the Kaplan-Meier curve, the pathway diagram, the pipeline
graphic.  Text extraction alone loses all of it, and for a scanned deck it
loses everything.

This stage renders the pages that need it and asks a vision model to
transcribe and describe them, producing a *composite* text view for each page
that downstream extraction consumes.  Pages that are pure text skip the model
entirely -- that is the single largest cost lever in the pipeline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.enums import PageKind, PipelineStage
from app.core.logging import get_logger
from app.llm import prompts
from app.llm.base import ImagePart
from app.llm.budgets import output_budget
from app.llm.client import LLMClient
from app.llm.schemas import PageUnderstandingOut
from app.pipeline.progress import NullProgress, ProgressReporter
from app.utils.text import truncate

log = get_logger(__name__)

#: Cap on text-layer characters shown to the vision model per page.
TEXT_LAYER_LIMIT = 6000


@dataclass(slots=True)
class PageInput:
    """Everything the understanding stage needs about one parsed page."""

    page_id: str
    page_number: int
    kind: PageKind
    text: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    render_path: Path | None = None
    image_count: int = 0
    vector_drawing_count: int = 0
    needs_vision: bool = False


@dataclass(slots=True)
class PageResult:
    page_id: str
    page_number: int
    understanding: PageUnderstandingOut | None
    used_vision: bool
    model: str | None = None
    error: str | None = None

    @property
    def slide_title(self) -> str | None:
        return self.understanding.slide_title if self.understanding else None


def composite_page_text(page: PageInput, result: PageResult | None) -> str:
    """Build the text view of a page that claim extraction reads.

    Sections are explicitly labelled so the extraction prompt (and a human
    reviewing a quote) can tell where a span came from -- the text layer, a
    mechanically-extracted table, or a vision reading of an image.  Claims
    quoting a ``[RECOVERED FROM IMAGE]`` span are marked ``from_visual``.
    """
    parts: list[str] = []
    if page.text.strip():
        parts.append(page.text.strip())

    for table in page.tables:
        markdown = (table or {}).get("markdown", "").strip()
        if markdown:
            parts.append(f"[TABLE]\n{markdown}")

    understanding = result.understanding if result else None
    if understanding is not None:
        recovered = understanding.recovered_text.strip()
        if recovered:
            parts.append(f"[RECOVERED FROM IMAGE]\n{recovered}")

        for element in understanding.visual_elements:
            details = [f"[FIGURE: {element.kind}] {element.description}"]
            if element.axis_labels:
                details.append(f"Axes: {'; '.join(element.axis_labels)}")
            if element.legend_entries:
                details.append(f"Legend: {'; '.join(element.legend_entries)}")
            parts.append("\n".join(details))

        if understanding.data_points:
            rows = []
            for point in understanding.data_points:
                unit = f" {point.unit}" if point.unit else ""
                estimated = " (estimated from axis)" if point.is_read_from_axis else ""
                context = f" [{point.context}]" if point.context else ""
                rows.append(f"- {point.label}: {point.value}{unit}{context}{estimated}")
            parts.append("[CHART VALUES]\n" + "\n".join(rows))

        # `vision_table`, not `table`: the loop above binds `table` to the
        # mechanically-extracted dicts from the text layer, and these are
        # ExtractedTable objects from the vision pass.
        for vision_table in understanding.tables:
            if vision_table.markdown.strip():
                heading = f" — {vision_table.title}" if vision_table.title else ""
                parts.append(f"[TABLE FROM IMAGE{heading}]\n{vision_table.markdown.strip()}")

    return "\n\n".join(parts).strip()


class PageUnderstandingStage:
    def __init__(
        self,
        llm: LLMClient,
        *,
        renders_dir: Path | None = None,
        progress: ProgressReporter | None = None,
    ) -> None:
        self.llm = llm
        self.renders_dir = renders_dir or settings.renders_dir
        self._progress: ProgressReporter = progress or NullProgress()

    async def run(self, pages: list[PageInput]) -> list[PageResult]:
        targets = [p for p in pages if self._should_use_vision(p)]
        skipped = [p for p in pages if p not in targets]
        log.info(
            "page_understanding.start",
            total_pages=len(pages),
            vision_pages=len(targets),
            text_only_pages=len(skipped),
        )

        results: dict[str, PageResult] = {
            page.page_id: PageResult(
                page_id=page.page_id,
                page_number=page.page_number,
                understanding=None,
                used_vision=False,
            )
            for page in skipped
        }

        if targets:
            # Admission is bounded here, not only inside the LLM client.
            # `gather` starts every page coroutine at once and each one reads
            # its render before its first await, so an unbounded gather holds
            # one decoded PNG per page of the deck in memory at the same time
            # -- the client's limit bounds requests in flight, not the bytes
            # queued behind them. Gating entry keeps that to `concurrency`
            # images, which is what makes a large deck safe on a small
            # instance.
            slots = asyncio.Semaphore(max(1, settings.llm_concurrency))
            completed = 0

            async def _bounded(page: PageInput) -> PageResult:
                nonlocal completed
                async with slots:
                    try:
                        return await self._understand(page)
                    finally:
                        completed += 1
                        # Reported per page: this stage carries 18% of the run
                        # and is where a large deck spends its first minutes.
                        self._progress.advance(
                            PipelineStage.PAGE_UNDERSTANDING, completed, len(targets)
                        )

            gathered = await asyncio.gather(
                *(_bounded(page) for page in targets), return_exceptions=True
            )
            for page, outcome in zip(targets, gathered, strict=True):
                if isinstance(outcome, BaseException):
                    log.warning(
                        "page_understanding.page_failed",
                        page_number=page.page_number,
                        error=str(outcome)[:300],
                    )
                    results[page.page_id] = PageResult(
                        page_id=page.page_id,
                        page_number=page.page_number,
                        understanding=None,
                        used_vision=False,
                        error=str(outcome)[:500],
                    )
                else:
                    results[page.page_id] = outcome

        return [results[page.page_id] for page in pages]

    def _should_use_vision(self, page: PageInput) -> bool:
        if page.kind is PageKind.EMPTY:
            return False
        if not page.needs_vision:
            return False
        # Without an image there is nothing for a vision model to read; the
        # offline provider still adds value by structuring the text layer.
        return page.render_path is not None or self.llm.is_degraded

    async def _understand(self, page: PageInput) -> PageResult:
        images: list[ImagePart] = []
        render = self._resolve_render(page)
        if render is not None:
            images.append(ImagePart(data=render.read_bytes()))

        tables_markdown = "\n\n".join(
            (t or {}).get("markdown", "") for t in page.tables if (t or {}).get("markdown")
        )
        user = prompts.render(
            "page_understanding",
            page_number=page.page_number,
            text_layer=truncate(page.text, TEXT_LAYER_LIMIT) or "(no text layer on this page)",
            tables=tables_markdown or "(no tables detected mechanically)",
        )

        understanding = await self.llm.structured(
            purpose="page_understanding",
            stage="page_understanding",
            system=prompts.system(),
            user=user,
            schema=PageUnderstandingOut,
            model=settings.model_vision,
            images=images,
            # One page, one budget -- transcription room plus the reserve the
            # model needs to think. Without a cap a page the model finds
            # confusing consumes the global 16k output allowance and minutes
            # of wall clock, while the pages behind it wait for a slot.
            max_output_tokens=output_budget(
                settings.model_vision,
                content_tokens=settings.vision_max_output_tokens,
                effort=settings.vision_reasoning_effort,
            ),
            reasoning_effort=settings.vision_reasoning_effort,
            context={
                "page_number": page.page_number,
                "page_text": page.text,
                "tables": page.tables,
                "has_images": page.image_count > 0,
                "vector_drawing_count": page.vector_drawing_count,
            },
        )
        return PageResult(
            page_id=page.page_id,
            page_number=page.page_number,
            understanding=understanding,
            used_vision=bool(images),
            model=settings.model_vision,
        )

    def _resolve_render(self, page: PageInput) -> Path | None:
        if page.render_path is None:
            return None
        path = page.render_path
        if not path.is_absolute():
            path = self.renders_dir / path
        return path if path.exists() else None
