"""Benchmark capture harness.

Runs a benchmark deck through the real pipeline and records the metrics that
define the baseline. Everything external is mocked and the LLM is the
deterministic stub, so two runs of the same deck on the same code produce the
same numbers: a diff in a captured metric is a diff in BioIntel, not noise.

Adding a benchmark company means adding a deck to ``DECKS`` -- see
docs/EVALUATION.md.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from app.analysis.claim_policy import policy_for
from app.analysis.verification import RegulatoryVerifier
from app.core.config import settings
from app.core.enums import (
    ADVERSE_CORROBORATION,
    CORROBORATED_STATUSES,
    UNCHECKED_CORROBORATION,
    VerifiabilityClass,
)
from app.db.models import AnalysisRun, Claim, ClaimAssessment, Report
from app.db.session import session_scope
from app.evidence.clinicaltrials import ClinicalTrialsClient
from app.evidence.europepmc import EuropePMCClient
from app.evidence.openfda import OpenFDAClient
from app.evidence.pubmed import PubMedClient
from app.evidence.retriever import EvidenceRetriever
from app.llm.base import Usage
from app.llm.client import LLMClient
from app.llm.pricing import cost_breakdown
from app.llm.stub_provider import StubProvider
from app.pipeline.orchestrator import AnalysisPipeline
from app.services.documents import store_document

#: Where captured runs are written. Committed, so a baseline change is a diff.
RESULTS_DIR = Path(__file__).resolve().parents[3] / "benchmarks"


@dataclass(frozen=True, slots=True)
class BenchmarkDeck:
    """One benchmark company and why it earns a place in the suite."""

    key: str
    company: str
    #: Returns the deck as PDF bytes.
    build: Callable[[], bytes]
    #: What regression this deck is here to catch. Kept with the deck so the
    #: rationale cannot drift away from the fixture.
    rationale: str


@dataclass
class BenchmarkRecord:
    """The captured baseline for one benchmark company."""

    company: str
    captured_at: str
    status: str

    # Runtime
    runtime_ms: int = 0
    runtime_by_stage_ms: dict[str, int] = field(default_factory=dict)

    # Assessment outcome
    total_score: float = 0.0
    recommendation: str = ""
    assessment_confidence: float = 0.0
    dimension_scores: dict[str, float] = field(default_factory=dict)

    # Report shape
    report_length_chars: int = 0
    report_sections: int = 0

    # Claims
    claims_total: int = 0
    verified_claims: int = 0
    contradicted_claims: int = 0
    proprietary_claims: int = 0
    verification_coverage: float = 0.0

    # Model usage
    #: Actual spend for this run. Structurally 0.0 in the benchmark suite: the
    #: deterministic stub reports model names with no price entry, which is
    #: correct (an unknown model must not be guessed at). Token counts below
    #: are real, and ``projected_cost_usd`` is what those tokens would cost.
    total_cost_usd: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    #: Captured tokens priced at the configured production reasoning model.
    #: A cost regression shows up here without needing a paid run.
    projected_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def evidence_transport(
    *,
    pubmed_xml: str,
    epmc_payload: dict,
    ctgov_payload: dict,
    openfda_payload: dict | None = None,
    pubmed_ids: list[str] | None = None,
) -> httpx.MockTransport:
    """A mock for every external source the pipeline consults."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "esearch" in url:
            ids = pubmed_ids or ["35675433"]
            return httpx.Response(
                200, json={"esearchresult": {"idlist": ids, "count": str(len(ids))}}
            )
        if "efetch" in url:
            return httpx.Response(200, text=pubmed_xml)
        if "europepmc" in url:
            return httpx.Response(200, json=epmc_payload)
        if "clinicaltrials" in url:
            return httpx.Response(200, json=ctgov_payload)
        if "api.fda.gov" in url:
            if openfda_payload is None:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
            return httpx.Response(200, json=openfda_payload)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


async def capture(deck: BenchmarkDeck, transport: httpx.MockTransport) -> BenchmarkRecord:
    """Run one benchmark deck end to end and record its baseline metrics."""
    with session_scope() as session:
        document, _ = store_document(session, data=deck.build(), filename=f"{deck.key}.pdf")
        session.flush()
        run = AnalysisRun(document_id=document.id)
        session.add(run)
        session.flush()
        run_id = run.id

    http = httpx.AsyncClient(transport=transport)
    retriever = EvidenceRetriever(
        pubmed=PubMedClient(http),
        europepmc=EuropePMCClient(http),
        clinicaltrials=ClinicalTrialsClient(http),
    )
    verifier = RegulatoryVerifier(
        openfda=OpenFDAClient(http), clinicaltrials=ClinicalTrialsClient(http)
    )
    llm = LLMClient(StubProvider(), run_id=run_id, persist_logs=False)
    await AnalysisPipeline(llm=llm, retriever=retriever, verifier=verifier).run(run_id)

    return _record(deck, run_id)


def _record(deck: BenchmarkDeck, run_id: str) -> BenchmarkRecord:
    with session_scope() as session:
        run = session.get(AnalysisRun, run_id)
        report = session.query(Report).filter_by(run_id=run_id).one_or_none()
        claims = session.query(Claim).filter_by(run_id=run_id).all()
        assessments = session.query(ClaimAssessment).filter_by(run_id=run_id).all()

        metrics = run.metrics or {}
        llm_metrics = metrics.get("llm", {})

        # Durations live in `timings` (ordered, one entry per stage run);
        # `stages` holds per-stage domain counts, not timing.
        by_stage = {
            str(t["stage"]): int(t.get("duration_ms", 0))
            for t in metrics.get("timings", [])
            if isinstance(t, dict) and "stage" in t
        }

        statuses = [a.corroboration_status for a in assessments]
        verified = sum(1 for s in statuses if s in CORROBORATED_STATUSES)
        contradicted = sum(1 for s in statuses if s in ADVERSE_CORROBORATION)
        unchecked = sum(1 for s in statuses if s in UNCHECKED_CORROBORATION)

        # "Proprietary" is a property of the claim type, not of the search:
        # these rest on data only the company holds, so a null result is
        # expected rather than adverse.
        proprietary = sum(
            1
            for c in claims
            if policy_for(c.claim_type).verifiability is VerifiabilityClass.COMPANY_INTERNAL
        )

        scored = verified + contradicted + unchecked
        coverage = (verified + contradicted) / scored if scored else 0.0

        prompt_tokens = int(llm_metrics.get("input_tokens", 0))
        completion_tokens = int(llm_metrics.get("output_tokens", 0))

        return BenchmarkRecord(
            company=deck.company,
            captured_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            status=run.status.value,
            runtime_ms=int(run.duration_ms or 0),
            runtime_by_stage_ms=by_stage,
            total_score=round(report.overall_score, 2) if report else 0.0,
            recommendation=(report.ic_recommendation or report.recommendation) if report else "",
            assessment_confidence=round(report.confidence, 3) if report else 0.0,
            dimension_scores=_dimension_scores(report),
            report_length_chars=len(report.markdown) if report else 0,
            report_sections=len(report.sections) if report else 0,
            claims_total=len(claims),
            verified_claims=verified,
            contradicted_claims=contradicted,
            proprietary_claims=proprietary,
            verification_coverage=round(coverage, 4),
            total_cost_usd=round(float(llm_metrics.get("estimated_cost_usd", 0.0)), 6),
            total_prompt_tokens=prompt_tokens,
            total_completion_tokens=completion_tokens,
            projected_cost_usd=round(
                cost_breakdown(
                    settings.model_reasoning,
                    Usage(input_tokens=prompt_tokens, output_tokens=completion_tokens),
                ).total_usd,
                4,
            ),
        )


def _dimension_scores(report: Report | None) -> dict[str, float | None]:
    """Per-dimension scores, keyed by dimension.

    An unassessed dimension carries a null score rather than a zero: "we had
    nothing to go on" and "we looked and it is bad" are different findings,
    and collapsing them here would hide exactly the regression the benchmark
    suite exists to catch.
    """
    if report is None or not report.scorecard:
        return {}
    return {
        str(d["dimension"]): (None if d.get("score") is None else round(float(d["score"]), 2))
        for d in report.scorecard.get("dimensions", [])
        if isinstance(d, dict) and "dimension" in d
    }


def write_results(records: list[BenchmarkRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "benchmarks": {r.company: r.to_dict() for r in records},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
