# BioIntel Evaluation Framework

This document defines how BioIntel is evaluated, benchmarked, and regression-tested. It establishes the baseline criteria, acceptable variance, and protocol for measuring improvements.

## Scope & Objective

**What BioIntel is designed to evaluate:**

1. **Scientific validity** of claims in biotech pitch decks
2. **Evidence quality** for those claims (from literature)
3. **Regulatory status** accuracy (FDA approvals, pipeline stage)
4. **Risk factors** that suggest implementation challenges
5. **Execution credibility** of the team and platform

**What BioIntel is NOT designed to evaluate:**

- Commercial potential or market size (marketing claims are flagged, not scored down)
- Team composition or track record (outside document scope)
- Financial projections (forward-looking, extracted but not scored)
- Patent landscape or freedom-to-operate (not covered by literature retrieval)
- Competitive positioning against other programs

## Benchmark Methodology

### Benchmark Companies

All five are implemented in `backend/tests/benchmarks/decks.py`. Each deck is
paired with a fixed set of external responses: a benchmark is a deck *and* the
world it is checked against, and both must be pinned for captured metrics to
mean anything.

| Company | Pages | Why it is in the suite | What it guards |
|---|---|---|---|
| BioNTech | 50 | Breadth: 12 programmes, heavy entity repetition across pages | Chunking, cross-chunk deduplication, runtime, truncated-output recovery |
| Moderna | 7 | Approved products alongside unverifiable track record — the deck that scored 24.1/100 "unsupported" | Separation of "we could not check" from "the evidence disagrees" |
| CRISPR Therapeutics | 8 | An approved therapy (CASGEVY) beside an early allogeneic and in vivo pipeline | A scorer that can only handle one epistemic regime at a time |
| Recursion | 8 | Platform capability claims with thin clinical evidence | The `plausible_unverified` path: 26 such claims, none contradicted |
| Beam Therapeutics | 8 | Proprietary preclinical data and editing-efficiency figures from internal assays | The `not_independently_verified` path: 37 such claims, none contradicted |

Decks are paraphrased from public disclosures for test purposes; they
reproduce the *shape* of each document rather than its bytes.

### Key Metrics

#### Correctness Metrics

- **Claim Extraction Accuracy**: % of material claims captured; false positive rate
- **Entity Deduplication**: Precision of disease/target/drug consolidation
- **Regulatory Status**: Correctly identifies FDA approvals, submissions, phases
- **Evidence Ranking**: Highest-impact literature is retrieved for top claims

#### Coverage Metrics

- **Verification Coverage**: % of verifiable claims checked against authoritative sources
- **Evidence Density**: Evidence records retrieved per major claim
- **Literature Gaps**: Categories for which no evidence was found (acceptable)

#### Quality Metrics

- **Corroboration Accuracy**: % adjudications that match domain expert judgment
- **False Positives**: Evidence marked as supporting when it actually contradicts
- **Confidence Calibration**: Reported confidence aligns with actual accuracy

#### Performance Metrics

- **Runtime**: Total pipeline duration (target: <15 min for 25-page deck)
- **Cost**: Total token spend and USD cost
- **Token Efficiency**: Tokens per claim; tokens per page
- **Retrieval Efficiency**: Queries per claim; records per query

## Baseline (v1-beta)

Captured **2026-07-28**. The machine-readable baseline is
`benchmarks/baseline.json`; this table is a view of it. Reproduce with:

```bash
make benchmark
```

### Scientific outcome

| Company | Score | Recommendation | Confidence |
|---|---|---|---|
| BioNTech | 74.42 | `advance_with_conditions` | 0.655 |
| Moderna | 74.77 | `advance_with_conditions` | 0.740 |
| CRISPR Therapeutics | 81.12 | `advance` | 0.725 |
| Recursion | 81.47 | `advance` | 0.781 |
| Beam Therapeutics | 60.66 | `significant_concerns` | 0.644 |

### Claims and verification

| Company | Claims | Verified | Contradicted | Proprietary | Coverage |
|---|---|---|---|---|---|
| BioNTech | 60 | 15 | 0 | 12 | 30.0% |
| Moderna | 16 | 7 | 0 | 2 | 63.6% |
| CRISPR Therapeutics | 20 | 9 | 0 | 1 | 56.3% |
| Recursion | 12 | 7 | 0 | 0 | 87.5% |
| Beam Therapeutics | 17 | 4 | 1 | 3 | 33.3% |

"Proprietary" counts claims whose type maps to
`VerifiabilityClass.COMPANY_INTERNAL` — they rest on data only the company
holds, so a null retrieval result is expected rather than adverse.

### Cost, tokens and report shape

| Company | Prompt tok | Completion tok | Projected cost | Report chars | Sections |
|---|---|---|---|---|---|
| BioNTech | 193,915 | 80,962 | $1.0520 | 19,899 | 9 |
| Moderna | 44,840 | 11,443 | $0.1705 | 18,514 | 9 |
| CRISPR Therapeutics | 74,986 | 17,839 | $0.2721 | 18,854 | 9 |
| Recursion | 40,026 | 10,682 | $0.1569 | 18,487 | 9 |
| Beam Therapeutics | 54,012 | 13,232 | $0.1998 | 19,941 | 9 |

**On cost.** Actual spend is structurally `$0.00` in the benchmark suite: the
deterministic stub reports model names with no price entry, and an unknown
model must never be guessed at. Token counts are real, so the baseline also
records `projected_cost_usd` — those tokens priced at the configured
production reasoning model (`gpt-5`). A cost regression is therefore visible
without a paid run.

**On runtime.** Recorded per company and per stage in `baseline.json`, but
deliberately *not* asserted: it is machine-dependent. Under the stub provider
the pipeline completes in seconds (BioNTech ~33s, the rest under 13s), because
no real model latency is involved. Runtime against a live provider is a
different measurement and is not what these numbers represent.

### Reproducibility

The suite is fully deterministic — stub provider, mocked sources, pinned
payloads. Repeated captures on unchanged code are bit-identical apart from
wall-clock runtime. A metric that moves is a change in BioIntel, not noise.

## Acceptable Variance

These are **enforced in code**, not aspirational. The thresholds below are
`EXACT` and `TOLERANCES` in `backend/tests/benchmarks/test_baseline.py`; that
module is the source of truth and this section describes it.

### Exact — must not change at all

| Metric | Why |
|---|---|
| `recommendation` | A conclusion, not a measurement. A flip is always a finding. |
| `status` | A benchmark that stops completing is a broken pipeline. |
| `report_sections` | Structural. A section appearing or vanishing is never noise. |

### Tolerated drift

| Metric | Tolerance | Rationale |
|---|---|---|
| `total_score` | 2% | Deterministic inputs; anything larger is a scoring change |
| `assessment_confidence` | 2% | As above |
| `verification_coverage` | 5% | Sensitive to retrieval and adjudication changes |
| `claims_total` | 5% | Extraction drift |
| `verified_claims` | 10% | Downstream of both extraction and retrieval |
| `contradicted_claims` | 10% | Small absolute counts; one claim can be 100% |
| `proprietary_claims` | 10% | Follows claim-type assignment |
| `report_length_chars` | 15% | Blunt but catches ballooning prose and truncation |
| `total_prompt_tokens` | 10% | Prompt and chunking changes |
| `total_completion_tokens` | 10% | As above |
| `projected_cost_usd` | 10% | Derived from the token counts above |

Tolerances are tight because the suite is deterministic. Against a live
provider they would need widening considerably — which is precisely why the
benchmark suite does not use one.

### Not asserted

- **Runtime.** Recorded per company and per stage, but machine-dependent.
  Track it as a trend; do not gate on it.
- **Actual cost.** Structurally zero under the stub provider; use
  `projected_cost_usd` instead.
- **Evidence ranking order** below the top of the list.
- **Prose wording**, provided stance and strength are unchanged.

### Failure output

A breach names the metric, both values, and the tolerance it broke:

```
Moderna regressed against the v1-beta baseline:
  recommendation: 'advance_with_conditions' -> 'significant_concerns' (must not change)
  total_score: 74.77 -> 61.0 (18.4% drift, tolerance 2%)
```

That output *is* the regression report — there is no separate artefact to
generate or forget to read.

## Regression Policy

### Triggers for Investigation

Any change that violates strict variance (above) or:

- Claim count drops by >10%
- Verification coverage drops by >5%
- Runtime increases by >20% without explanation
- Cost per deck increases by >15%
- Any claim whose regulatory status is now wrong

### Investigation Process

1. **Reproduce**: Run the benchmark deck on both versions
2. **Isolate**: Identify which pipeline stage regressed
3. **Root Cause**: Determine if change is intentional (code) or incidental (config)
4. **Assessment**: Decide if regression is acceptable given tradeoff
5. **Document**: Record in regression_report.md with:
   - What changed
   - Why (if intentional)
   - Impact on downstream metrics
   - Action taken (reverted / accepted / investigated)

### Acceptable Regressions

- **Performance trade-offs**: Slower but more accurate (decided in advance)
- **Scope changes**: Retrieving different literature if quality improves
- **Config changes**: Token limits, reasoning effort, retrieval depth (document reason)
- **Expected variance**: Seasonal in literature (new papers published)

### Unacceptable Regressions

- **Silent failures**: Accuracy drops without explanation
- **Cost increases**: Spending more tokens for same output
- **Correctness bugs**: Regulatory data now wrong
- **Unexplained regressions**: No documented reason for change

## Expected ranges

What a healthy v1-beta run looks like. A capture outside these bands is not
automatically wrong, but it needs an explanation.

| Metric | Expected range | Reading if outside |
|---|---|---|
| Total score | 55–85 | Below 55 across all decks suggests unverified claims are being penalised again |
| Assessment confidence | 0.60–0.80 | A jump above 0.85 with unchanged coverage means confidence has decoupled from evidence |
| Verification coverage | 30–90% | Near 0% across all decks means retrieval is broken, not that the companies are unsupported |
| Claims per deck | 12–60 | Scales with deck size; a collapse means extraction truncation |
| Contradicted claims | 0–2 | These decks are largely accurate; many contradictions means false-positive adjudication |
| Report length | 18k–20k chars | Outside this, prose control has drifted |
| Sections | exactly 9 | Structural |

Recommendations should span at least two categories across the suite. If all
five decks return the same recommendation, the routing logic has probably
collapsed to keying off the score alone — the failure this design exists to
prevent.

## Comparative Benchmarking

### How to compare two versions of BioIntel

The suite is designed so this is a diff, not an exercise.

```bash
git checkout <old-ref> && make benchmark   # writes benchmarks/results.json
cp benchmarks/results.json /tmp/old.json
git checkout <new-ref> && make benchmark
diff <(jq -S . /tmp/old.json) <(jq -S . benchmarks/results.json)
```

Because captures are deterministic, every line of that diff is a real
behavioural change. Ignore `captured_at` and `runtime_ms`.

The stronger form: leave `benchmarks/baseline.json` at the old version and
just run `make benchmark` on the new one. Anything outside tolerance fails
with old value, new value and tolerance — which is the comparison already
done for you.

### Reproducible benchmarking

The suite pins everything that could otherwise drift:

- **Model**: the deterministic stub provider, not a live model.
- **External sources**: fixed payloads per company in `decks.py`; outbound
  HTTP is blocked in tests.
- **Documents**: decks are generated from committed code, not binary files.
- **Configuration**: `tests/conftest.py` sets the environment explicitly.

The only non-reproducible metric is wall-clock runtime, which is why it is
recorded but never asserted.

To benchmark against a live provider — a different and more expensive
measurement — set real credentials and run the pipeline via
`make generate-report pdf=<deck>`, then read `run_metrics.json` and
`run_summary.md` from the run's metrics directory. Do not compare those
numbers to the baseline in this document; they measure different things.

## Adding New Benchmark Companies

### Process

A new company must earn its place: it has to guard a failure mode no existing
deck guards. Five decks that all exercise the same path cost runtime and catch
nothing extra.

1. **Identify the gap.** Name the regression the suite cannot currently
   catch — an evidence state, an archetype, a document shape.
2. **Add the deck.** Create `backend/tests/fixtures/<company>_deck.py`
   exporting a `<company>_pdf()` builder. Paraphrase public disclosures; do not
   reproduce a real deck verbatim. Put the rationale in the module docstring.
3. **Pin its world.** Add an entry to `_TRANSPORTS` in
   `backend/tests/benchmarks/decks.py` with the PubMed, Europe PMC,
   ClinicalTrials.gov and openFDA responses this deck should meet. Responses
   should be realistic, including the negative cases — an openFDA 404 for a
   CBER-licensed vaccine is a feature, not an oversight.
4. **Register it.** Append a `BenchmarkDeck` to `DECKS` with `key`, `company`,
   `build` and `rationale`. The rationale lives with the deck so it cannot
   drift away from the fixture.
5. **Capture and review.** Run `make benchmark`. The new company is skipped
   against the baseline (it is not in it yet) but its metrics are written to
   `benchmarks/results.json`. **Read them.** Confirm the recommendation,
   coverage and evidence-state distribution are what the deck was built to
   produce — a benchmark recording the wrong behaviour is worse than none.
6. **Accept.** `make benchmark-accept` re-records `benchmarks/baseline.json`.
   Commit the deck, the transport and the baseline together, with the
   rationale in the commit message.

No new test file is needed: `tests/benchmarks/test_baseline.py` is
parametrised over `DECKS` and picks the company up automatically.

Add a dedicated `tests/integration/test_<company>_regression.py` only when the
deck needs assertions the generic metric comparison cannot express — as
BioNTech and Moderna do, because they pin specific behavioural fixes rather
than aggregate numbers.

## Limitations & Known Issues

1. **Regulatory database gaps**: Drugs@FDA does not cover CBER vaccines; resolve as "not independently verified"
2. **Retrieval currency**: PubMed updates lag real-world by weeks; new trials may not appear immediately
3. **Preprints not included**: Conference abstracts, EMA submissions, non-US registries outside scope
4. **Abstract reading only**: Full-text papers could reveal nuances abstract misses
5. **Seasonal drift**: New literature published; old findings may be superseded (not a regression)

## Success Criteria

BioIntel is considered production-ready when:

1. ✓ All benchmark companies pass regression tests
2. ✓ Regulatory status accuracy ≥ 98%
3. ✓ Evidence false-positive rate < 5%
4. ✓ Runtime ≤ 15 min per 25-page deck with full LLM
5. ✓ Cost ≤ $8 USD per deck (full analysis)
6. ✓ Verification coverage ≥ 30% (claims that can be verified)
7. ✓ Confidence calibration within ±10% (reported vs. actual)

## Roadmap for v1.1, v1.2, v2.0

### v1.1 (Q3 2026)

- Add CRISPR Therapeutics benchmark
- Reduce runtime by 20% (parallelization, caching)
- Improve entity deduplication precision to 95%
- Add EMA pipeline stage verification

### v1.2 (Q4 2026)

- Add Recursion + Beam Therapeutics benchmarks
- Support for non-US trial registries
- Confidence model recalibration on expanded benchmark set
- Cost reduction through prompt optimization

### v2.0 (H1 2027)

- Full-text paper retrieval and analysis
- Patent landscape assessment
- Team track-record verification (structured data)
- Real-time regulatory monitoring for live portfolios
