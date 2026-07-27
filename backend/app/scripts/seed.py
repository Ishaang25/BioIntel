"""Analyse the bundled sample deck end to end.

    python -m app.scripts.seed [--pdf path/to/deck.pdf] [--no-retrieval]

Useful for a first run on a new machine, for demonstrating the product, and
for checking that a configuration change has not broken the pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.models import AnalysisRun
from app.db.session import create_all, session_scope
from app.pipeline.orchestrator import AnalysisPipeline
from app.services.documents import store_document

log = get_logger(__name__)


def _sample_pdf() -> bytes:
    from tests.fixtures.sample_deck import neurogen_pdf

    return neurogen_pdf()


async def main_async(pdf_path: Path | None, retrieval: bool) -> int:
    configure_logging()
    create_all()

    if not retrieval:
        object.__setattr__(settings, "retrieval_enabled", False)

    if pdf_path is not None:
        if not pdf_path.exists():
            print(f"No such file: {pdf_path}", file=sys.stderr)
            return 2
        data = pdf_path.read_bytes()
        filename = pdf_path.name
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        data = _sample_pdf()
        filename = "neurogen-sample.pdf"

    with session_scope() as session:
        document, created = store_document(session, data=data, filename=filename)
        session.flush()
        run = AnalysisRun(document_id=document.id, requested_by="seed-script")
        session.add(run)
        session.flush()
        run_id, document_id = run.id, document.id

    print(f"document {document_id} ({'new' if created else 'existing'})")
    print(f"run      {run_id}")
    if settings.llm_provider == "stub":
        # ASCII only: Windows consoles default to cp1252 and a stray glyph
        # would crash the script rather than print a warning.
        print("\n[!] No OPENAI_API_KEY configured - running the deterministic offline")
        print("    analyser. The pipeline exercises end to end but the analysis is")
        print("    lexical, not scientific, and the report is marked degraded.\n")

    context = await AnalysisPipeline().run(run_id)

    print("\n--- stage metrics -------------------------------------------------")
    for stage, metrics in context.stage_metrics.items():
        print(f"  {stage:<20} {metrics}")

    if context.overall:
        print(
            f"\nscientific credibility: {context.overall.score:.1f}/100 "
            f"({context.overall.band.value}), confidence {context.overall.confidence:.2f}"
        )
    print(f"claims: {len(context.claim_ids)}   evidence: {len(context.evidence_ids)}")
    if context.warnings:
        print("\nwarnings:")
        for warning in context.warnings:
            print(f"  - {warning}")

    print(f"\nOpen http://localhost:3000/runs/{run_id} to review the analysis.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=None, help="analyse this PDF instead")
    parser.add_argument(
        "--no-retrieval", action="store_true", help="skip external literature retrieval"
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args.pdf, not args.no_retrieval))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
