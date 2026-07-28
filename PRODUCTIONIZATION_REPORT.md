# BioIntel Productionization — Engineering Report

**Version:** v1-beta
**Date:** 2026-07-28
**Scope:** Phases 1–11, productionization of the feature-complete Beta

---

## 1. Summary of work completed

This pass began as a verification of work reported complete in an earlier
productionization attempt, and became a repair of it. The central deliverable
of that attempt — the instrumentation layer — had never executed successfully,
and the benchmark baseline and evaluation documentation contained figures that
could not have been measured.

**The headline finding.** `_save_run_metrics` referenced four attributes that
do not exist on the domain objects it reads. It raised on every run, and a
broad `except Exception` downgraded the failure to a warning. No run had ever
produced `run_metrics.json` or `run_summary.md`; `storage/` contained no
metrics directory at all. Phases 2 and 3 were reported complete and were
non-functional.

The rest followed from checking rather than trusting:

- Reported "lint passes" was true only for a narrower scope than CI uses, and
  CI's `ruff format --check app tests` was **failing** on the two files the
  instrumentation commit had touched.
- `mypy` aborted before checking any project code, and had done so for as long
  as numpy has been a dependency. The old baseline recorded typecheck as
  "PENDING"; it was broken.
- The benchmark baseline recorded test-suite pass counts rather than any of the
  13 metrics Phase 1 asks for, for 2 of the 5 benchmark companies.
- `docs/EVALUATION.md` gave BioNTech as 63 claims / 27 verified / 43% coverage.
  Measured: 60 / 15 / 30%. `MetricsCollector` was instantiated on every run and
  never called.

**What the repository looks like now.** The instrumentation works and is
tested. All five benchmark companies are captured end to end, deterministically,
with the required metrics, against a committed baseline proven to catch drift.
The four missing Phase 8 documents exist. Every figure in the documentation was
read from the code or the baseline.

**Scientific behaviour is unchanged.** No scoring, extraction, retrieval or
recommendation logic was altered. Benchmark captures are bit-identical before
and after every refactor in this pass — which is how the two variable renames
were shown to be behaviour-preserving.

### Phase status

| Phase | Status | Note |
|---|---|---|
| 1 — Freeze | ✓ Redone | Baseline rebuilt with real metrics, all 5 companies |
| 2 — Instrumentation | ✓ Repaired | Was non-functional; now works and is tested |
| 3 — Dashboard | ✓ Repaired | `run_summary.md` now actually produced |
| 4 — Regression suite | ✓ Built | 5 companies, tolerances, drift detection verified |
| 5 — Cleanup | ✓ Done | Dead code removed, type-changing names fixed |
| 6 — Configuration | ✓ Inherited, reviewed | `docs/CONFIGURATION.md` sound |
| 7 — Developer experience | ✓ Completed | Missing targets added; `lint` now format-checks |
| 8 — Documentation | ✓ Completed | 3 documents written, 1 moved, 2 corrected |
| 9 — Evaluation | ✓ Rewritten | Against measured data |
| 10 — Audit | ✓ Done | Type checker repaired; real defects fixed |
| 11 — Readiness | ✓ Rewritten | Honest verdict replacing "APPROVED FOR PRODUCTION" |

---

## 2. Git commits (chronological)

| Commit | Title |
|---|---|
| `4556b54` | Fix instrumentation layer: it never ran successfully |
| `e4c6146` | Replace the benchmark baseline with measured numbers; add all 5 companies |
| `b76778e` | Add the four missing Phase 8 documents; correct EVALUATION.md |
| `dab819a` | Repair the type checker; fix what it found |

Inherited from the prior pass and left in history: `9c94cb0`, `da59387`,
`19cc482`, `f310419`, `292e1ae`.

Tag `v1-beta` already existed and was left in place; `benchmarks/baseline.json`
is what now defines v1-beta behaviour in practice.

---

## 3. Files added

**Benchmark framework**
- `backend/tests/benchmarks/harness.py` — capture harness, `BenchmarkRecord`, mock transport builder
- `backend/tests/benchmarks/decks.py` — the five companies and their pinned external responses
- `backend/tests/benchmarks/test_baseline.py` — capture and regression assertions
- `backend/tests/benchmarks/__init__.py`
- `benchmarks/baseline.json` — the measured v1-beta baseline

**Benchmark decks**
- `backend/tests/fixtures/moderna_deck.py` (extracted from the regression test)
- `backend/tests/fixtures/crispr_deck.py`
- `backend/tests/fixtures/recursion_deck.py`
- `backend/tests/fixtures/beam_deck.py`

**Tests**
- `backend/tests/unit/test_instrumentation.py` — 21 tests

**Documentation**
- `docs/SCORING.md`
- `docs/RETRIEVAL.md`
- `docs/REPORTING.md`

**Developer experience**
- `scripts/clean.py`
- `scripts/run_stack.py`

---

## 4. Files modified

| File | Change |
|---|---|
| `backend/app/pipeline/orchestrator.py` | Corrected every invented attribute in the metrics writer; real cost split; populated fields hardcoded to zero; metrics failures logged at error with traceback |
| `backend/app/core/instrumentation.py` | Removed dead `MetricsCollector`; honest cost rendering; formatting |
| `backend/app/llm/pricing.py` | Added `CostBreakdown` and `cost_breakdown()`; `estimate_cost_usd` derives from it |
| `backend/app/llm/client.py` | Accumulate cost breakdown and per-stage cached/reasoning tokens |
| `backend/app/core/enums.py` | Added `CORROBORATED_STATUSES` alongside existing groupings |
| `backend/app/pipeline/context.py` | Removed dead metrics-collector field and factory |
| `backend/app/jobs/worker.py` | Reject jobs with no `run_id` up front, permanently |
| `backend/app/extraction/entities.py` | Renamed a variable that changed type under one name |
| `backend/app/extraction/page_understanding.py` | Same |
| `backend/pyproject.toml` | Exclude generated alembic revisions from ruff; `benchmark` marker; mypy `python_version` 3.12 |
| `backend/alembic/env.py` | Import order |
| `Makefile` | `install`, `run`, `generate-report`, `benchmark`, `benchmark-accept`; `lint` format-checks; portable `clean` |
| `.gitignore` | Ignore `benchmarks/results.json` |
| `README.md` | Documentation index |
| `docs/EVALUATION.md` | Measured baseline; real tolerances; expected ranges; rewritten add-a-company and version-comparison sections |
| `docs/PRODUCTION_READINESS.md` | Measured performance; honest readiness verdict; measured vs unmeasured criteria |
| `backend/tests/integration/test_pipeline.py` | Artefact and cost-split assertions |
| `backend/tests/integration/test_worker.py` | Malformed-job test |
| `backend/tests/integration/test_moderna_regression.py` | Use the shared deck fixture |

---

## 5. Files removed

- `benchmarks/baseline_v1_beta.json` — recorded test-suite counts, not benchmark metrics
- `benchmarks/.gitkeep`
- `ARCHITECTURE.md` — moved to `docs/ARCHITECTURE.md`
- `MetricsCollector` and `_make_metrics_collector` (code, not files)

---

## 6. Instrumentation added

Written automatically to `storage/metrics/{run_id}/` after every run:

- `run_metrics.json` — structured metrics
- `run_summary.md` — human-readable execution profile

Captured: per-stage duration and status; per-stage prompt, completion, cached
and reasoning tokens; LLM calls and latency; pages and OCR requirement;
entities extracted and deduplicated; claims total, verified and scored;
corroborated, contradicted and unverified counts; evidence retrieved and
ranked; unique sources; verification coverage; assessment confidence; report
sections, length, references and questions; degradation flag and warnings;
provider; and estimated cost split into input, cached input, completion and
reasoning.

**Cost correctness.** The previous implementation set input and completion cost
each to half the total. On the configured model, output tokens cost **eight
times** input tokens, so the reported split was wrong by construction. Both are
now derived from the same per-model prices the total uses, accumulated per call.
Reasoning spend is rendered as "of which" — those tokens bill at the completion
rate and are already inside it.

Failures in the metrics writer now log at error with a traceback rather than
warning, which is the change that would have surfaced the original defect.

---

## 7. Performance

No optimisation was attempted, deliberately: the profiling data needed to
target it did not exist until the instrumentation worked. It now does, and the
first real measurement is unambiguous.

Aggregated across all five benchmarks:

| Stage | Share of runtime |
|---|---|
| Retrieval | 73.9% |
| Assessment | 18.0% |
| Parse | 2.9% |
| Claims | 2.2% |
| Entities | 1.7% |
| Everything else | <1.5% combined |

Retrieval dominates on every deck (60.4%–83.1%). This is with the deterministic
stub, which removes model latency — under a live provider the extraction and
reasoning stages grow and retrieval's share falls. The defensible conclusion:
**retrieval is the dominant cost of BioIntel's own work**, and is where
optimisation should start.

| Company | Runtime | Prompt tok | Completion tok | Projected cost |
|---|---|---|---|---|
| BioNTech (50pp) | 33.1s | 193,915 | 80,962 | $1.0520 |
| CRISPR Tx | 12.8s | 74,986 | 17,839 | $0.2721 |
| Recursion | 5.7s | 40,026 | 10,682 | $0.1569 |
| Beam | 5.5s | 54,012 | 13,232 | $0.1998 |
| Moderna | 4.2s | 44,840 | 11,443 | $0.1705 |

Projected cost prices the real token counts at the configured production model.
Actual spend is structurally $0 under the stub, which correctly refuses to
price an unknown model.

---

## 8. Repository cleanup

- Removed `MetricsCollector` (instantiated on every `RunContext`, never called)
  and its unused factory. It duplicated the pre-existing `stage_timings`
  mechanism the orchestrator actually uses.
- Extracted the Moderna deck from inside its regression test into a shared
  fixture, so harness and test cannot drift apart.
- Renamed two variables rebound to a different type under the same name —
  `entities` (`ExtractedEntity` → `ResolvedEntity`) and `table`
  (dict → `ExtractedTable`). Both correct at runtime; both hid a type change.
- Made ruff's configuration honest: generated alembic revisions excluded, so a
  bare `ruff check .` agrees with `make lint` instead of reporting 64 errors
  nobody was expected to fix.
- Moved `ARCHITECTURE.md` into `docs/`.

No large-scale refactoring was undertaken, per the brief.

---

## 9. Documentation created

| Document | Status |
|---|---|
| `docs/ARCHITECTURE.md` | Moved from root |
| `docs/SCORING.md` | **New** — dimensions, evidence states, aggregation, confidence, recommendation ladder, how scores move |
| `docs/RETRIEVAL.md` | **New** — sources, ranking, registry verification, caching, failure handling |
| `docs/REPORTING.md` | **New** — section ownership, traceability, driver bullets, question ranking |
| `docs/EVALUATION.md` | Substantially rewritten against measured data |
| `docs/PRODUCTION_READINESS.md` | Performance and readiness sections rewritten |
| `docs/CONFIGURATION.md` | Inherited, reviewed |
| `docs/DEPLOYMENT.md` | Inherited, reviewed |
| `README.md` | Documentation index added |

Every number was read from the code or baseline at the time of writing. Where a
first draft disagreed with the source it was corrected — the credibility band
cutoffs are 75/60/45/30, not the 80/65/50/35 initially written.

---

## 10. Regression framework

Five companies, each a deck *and* a pinned set of external responses, captured
end to end through the real pipeline.

| Company | Guards |
|---|---|
| BioNTech | Chunking, cross-chunk dedup, runtime, truncated-output recovery |
| Moderna | "Could not check" vs "evidence disagrees" — the 24.1/100 defect |
| CRISPR Therapeutics | An approved therapy beside a preclinical pipeline |
| Recursion | The `plausible_unverified` path |
| Beam | The `not_independently_verified` path |

Exact-match: `recommendation`, `status`, `report_sections`. Tolerated drift:
score and confidence 2%; coverage and claim count 5%; verified / contradicted /
proprietary claims and tokens 10%; report length 15%.

A failure names the metric, both values and the tolerance broken — the
regression report *is* the test output. **Verified to work** by perturbing the
baseline:

```
Moderna regressed against the v1-beta baseline:
  recommendation: 'advance_with_conditions' -> 'significant_concerns' (must not change)
  total_score: 74.77 -> 61.0 (18.4% drift, tolerance 2%)
```

Determinism confirmed by repeated capture: bit-identical apart from wall-clock
runtime.

---

## 11. Benchmark status — **PASS**

| Company | Score | Recommendation | Confidence | Claims | Verified | Contradicted | Proprietary | Coverage |
|---|---|---|---|---|---|---|---|---|
| BioNTech | 74.42 | `advance_with_conditions` | 0.655 | 60 | 15 | 0 | 12 | 30.0% |
| Moderna | 74.77 | `advance_with_conditions` | 0.740 | 16 | 7 | 0 | 2 | 63.6% |
| CRISPR Tx | 81.12 | `advance` | 0.725 | 20 | 9 | 0 | 1 | 56.3% |
| Recursion | 81.47 | `advance` | 0.781 | 12 | 7 | 0 | 0 | 87.5% |
| Beam | 60.66 | `significant_concerns` | 0.644 | 17 | 4 | 1 | 3 | 33.3% |

Three recommendation categories across five decks — routing has not collapsed
to keying off the score alone. Moderna sits at 74.77 `advance_with_conditions`,
not the 24.1 "unsupported" that motivated the corroboration rework.

**Suite:** 553 tests passing (421 unit, 132 integration), 5/5 benchmark
captures passing, `ruff check .` and `ruff format --check app tests` clean.

---

## 12. Remaining technical debt

1. **`orchestrator.py` is ~2,000 lines.** Stage implementations, metrics
   assembly and helpers in one module. The largest readability win available,
   and out of scope for a pass told not to redesign.
2. **13 mypy errors remain**, all SQLAlchemy stub limitations
   (`Result.rowcount`, `FromClause.delete`). They must be suppressed
   deliberately before type checking can be a CI gate; it is advisory today.
3. **`app/scripts/seed.py` imports from `tests/`** for its sample deck. The
   application package should not depend on the test package, even lazily.
4. **`make` is unavailable on the Windows development box.** The Makefile is
   the documented interface but cannot be invoked there without installing GNU
   Make. The helper scripts work standalone; a cross-platform task runner would
   close this properly.
5. **Benchmark cost is a projection.** Real spend is structurally $0 under the
   stub. Correct and clearly labelled, but provider-side cost regressions
   (caching, reasoning effort) are invisible to the suite.
6. **`storage/` accumulates render directories** with no retention policy.

---

## 13. Remaining known limitations

Scientific limitations, unchanged and documented in the README and in every
report:

- Adjudication reads abstracts, not full texts.
- Retrieval is keyword-driven; proprietary unpublished work correctly returns
  nothing, reported as absence of evidence.
- Chart values carry reading error and are flagged as estimated.
- Corpus is PubMed, Europe PMC, ClinicalTrials.gov and openFDA — no patents, no
  conference abstracts, no EMA, no non-US/EU registries.
- Drugs@FDA does not index CBER-licensed vaccines, so those approvals resolve
  to *not independently verified*.
- Scoring priors and weights encode a defensible view, not a fact. They live in
  readable tables so they can be argued with.

Stated newly, and more important than any of the above:

- **There is no ground-truth set, so accuracy has never been measured.** The
  benchmarks prove BioIntel is *consistent*, not that it is *right*. No figure
  in this repository should be read as an accuracy claim.
- **BioIntel has never run in production.** There is no uptime, latency or
  live-provider cost data.

---

## 14. Roadmap

### v1.1 — Make accuracy measurable

**Critical**
- Build an expert-labelled ground-truth set over the five benchmark decks:
  correct claim type, verifiability, and corroboration outcome. Everything else
  here is guesswork without it.
- Measure the evidence-adjudication false-positive rate against it.
- One live-provider benchmark run: real runtime, cost and latency.

**Important**
- Suppress the SQLAlchemy stub errors; make `mypy` a CI gate.
- Retrieval optimisation, targeted by the 73.9% measurement: raise cross-run
  cache hit rate, batch queries.
- Break up `orchestrator.py`.

**Nice to have**
- Cross-platform task runner.
- Storage retention policy.

### v1.2 — Broaden the evidence base

**Important**
- Full-text retrieval where open access permits, instead of abstracts only.
- EMA and additional registries.
- Prompt caching, measured against the now-instrumented cached-token counts.
- Two or three more benchmark companies for failure modes the current five miss
  — a genuinely weak company, and a non-biomedical one to exercise archetype
  routing.

**Nice to have**
- Conference abstracts and preprints as a separately-graded tier.
- Scheduled benchmark capture in CI with drift alerting.

### v2.0 — Earn unsupervised operation

**Critical**
- Sustained accuracy measurement against reviewed output, enough to state a
  defensible error rate.
- Calibration study: do the confidence bands mean what they claim?

**Important**
- Multi-document analysis — deck plus data room plus publications as one
  evidence graph.
- Analyst feedback loop that adjusts retrieval, never scoring priors silently.
- Horizontal scaling and high availability.

**Nice to have**
- Portfolio-level views across analysed companies.
- Interactive evidence-graph exploration.

---

## 15. What to check first, if you are reviewing this

The uncomfortable lesson from this pass: a green test suite and a confident
report proved nothing about whether the instrumentation ran. Three commands
guard against a repeat, each under a minute.

```bash
make lint
```

```bash
make test
```

```bash
make benchmark
```

`make lint` now includes the format check CI actually runs. `make test`
asserts `run_metrics.json` and `run_summary.md` exist after a run — an
assertion that did not exist before, which is why the defect survived a phase
claiming to have delivered it. `make benchmark` checks five companies against
a committed baseline and names any drift.
