"""Benchmark capture and regression assertions.

Running this module rewrites ``benchmarks/results.json`` with what the current
code actually produces, and compares it against the committed baseline in
``benchmarks/baseline.json``. A metric that moves outside its tolerance fails
here with the old value, the new value, and the tolerance it broke, so a
regression report is the test output rather than a separate artefact.

Marked ``benchmark`` and run by ``make benchmark``; excluded from the default
suite because a full capture runs the whole pipeline once per company.
"""

from __future__ import annotations

import json

import pytest

from tests.benchmarks.decks import DECKS, transport_for
from tests.benchmarks.harness import RESULTS_DIR, capture, write_results

BASELINE_PATH = RESULTS_DIR / "baseline.json"
RESULTS_PATH = RESULTS_DIR / "results.json"

#: How far a metric may move before it is a regression rather than noise.
#: Values are fractions of the baseline unless noted. Runtime and cost are
#: deliberately loose (machine-dependent); the scientific outputs are tight,
#: because the deterministic stub provider makes them reproducible.
TOLERANCES: dict[str, float] = {
    "total_score": 0.02,
    "assessment_confidence": 0.02,
    "verification_coverage": 0.05,
    "claims_total": 0.05,
    "verified_claims": 0.10,
    "contradicted_claims": 0.10,
    "proprietary_claims": 0.10,
    "report_length_chars": 0.15,
    "total_prompt_tokens": 0.10,
    "total_completion_tokens": 0.10,
    "projected_cost_usd": 0.10,
}

#: Metrics that must match exactly -- these are conclusions, not measurements.
EXACT: tuple[str, ...] = ("status", "recommendation", "report_sections")


@pytest.fixture(scope="module")
def captured(request) -> dict:
    """Capture every benchmark once, then share it across the assertions."""
    return request.config.stash[_STASH_KEY]


_STASH_KEY = pytest.StashKey[dict]()


@pytest.mark.benchmark
@pytest.mark.parametrize("deck", DECKS, ids=lambda d: d.key)
async def test_benchmark_capture(deck, request, settings, monkeypatch):
    """Run one benchmark deck and record its metrics."""
    monkeypatch.setattr(settings, "retrieval_enabled", True)
    monkeypatch.setattr(settings, "regulatory_verification_enabled", True)

    record = await capture(deck, transport_for(deck.key))

    assert record.status == "succeeded", f"{deck.company} did not complete"
    assert record.claims_total > 0, f"{deck.company} produced no claims"
    assert record.report_length_chars > 0, f"{deck.company} produced no report"

    store = request.config.stash.setdefault(_STASH_KEY, {})
    store[deck.company] = record

    _compare_to_baseline(deck.company, record)


def _compare_to_baseline(company: str, record) -> None:
    if not BASELINE_PATH.exists():
        pytest.skip("no baseline committed yet; run `make benchmark-accept` to create one")

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["benchmarks"].get(company)
    if baseline is None:
        pytest.skip(f"{company} is not in the baseline yet")

    current = record.to_dict()
    failures: list[str] = []

    for metric in EXACT:
        if current[metric] != baseline[metric]:
            failures.append(
                f"{metric}: {baseline[metric]!r} -> {current[metric]!r} (must not change)"
            )

    for metric, tolerance in TOLERANCES.items():
        old, new = baseline[metric], current[metric]
        if old == 0:
            if new != 0:
                failures.append(f"{metric}: 0 -> {new} (baseline was zero)")
            continue
        drift = abs(new - old) / abs(old)
        if drift > tolerance:
            failures.append(
                f"{metric}: {old} -> {new} ({drift:.1%} drift, tolerance {tolerance:.0%})"
            )

    if failures:
        pytest.fail(
            f"{company} regressed against the v1-beta baseline:\n  "
            + "\n  ".join(failures)
            + "\n\nIf the change is intended, re-record with `make benchmark-accept` "
            "and explain the movement in the commit message."
        )


@pytest.fixture(scope="module", autouse=True)
def _write_results(request):
    """Persist whatever was captured, even if an assertion failed."""
    yield
    store = request.config.stash.get(_STASH_KEY, None)
    if store:
        write_results(list(store.values()), RESULTS_PATH)
