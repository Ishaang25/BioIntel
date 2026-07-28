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

| Company | Deck Pages | Rationale | Expected Characteristics |
|---------|-----------|-----------|--------------------------|
| BioNTech | 25 | Approved vaccine, real trials, established platform | High claim density; multiple modalities; extensive trial data available |
| Moderna | 7 | FDA approval, active pipeline, diverse indications | Regulatory status is testable; forward guidance mixed with facts |
| CRISPR Therapeutics | — | (planned) Base editing platform; multiple programs | Platform differentiation; early stage programs |
| Recursion | — | (planned) High-throughput screening + AI; discovery-to-clinic | Complex evidence graph; computational claims |
| Beam Therapeutics | — | (planned) Base editing, rare diseases | Translational claims; regulatory pathway clarity |

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

Established on: **2026-07-28**

### BioNTech Regression Test

| Metric | Value | Status |
|--------|-------|--------|
| Pages | 25 | ✓ parsed |
| Entities Extracted | ~220 | baseline |
| Claims Extracted | 63 | baseline |
| Claims Verified | 27 (43%) | baseline |
| Evidence Records | 156 | baseline |
| Test Runtime | 34.63s | baseline |
| Test Pass Rate | 17/17 | ✓ pass |

### Moderna Regression Test

| Metric | Value | Status |
|--------|-------|--------|
| Pages | 7 | ✓ parsed |
| Claims Extracted | 18 | baseline |
| Claims Corroborated | 6 | baseline |
| Test Runtime | 91.91s | baseline |
| Test Pass Rate | 21/21 | ✓ pass |

## Acceptable Variance

### Metric Categories

#### Strict (no regression allowed)

These affect user trust and must remain stable:

- **Regulatory status correctness**: Must remain 100% accurate
- **FDA approval lookup**: Must match Drugs@FDA exactly
- **Trial registry status**: Must match ClinicalTrials.gov exactly
- **False positive rate** (evidence marked as supporting when contradictory): Must not increase

#### Moderate (drift of ±5% acceptable)

- Claim extraction count (±5%)
- Entity deduplication precision (±3%)
- Test runtime (±10% for regression tests)
- Token count per run (±5%, accounting for prompt changes)

#### Flexible (optimization allowed)

- Evidence ranking order (as long as top-3 are most relevant)
- Corroboration wording (as long as stance and strength unchanged)
- Report section order (as long as all sections present)

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

## Comparative Benchmarking

### How to Compare Two Versions

1. **Same environment**: Same API keys, models, configuration
2. **Same deck**: Use v1-beta benchmark decks only
3. **Run both**: Generate run_metrics.json for each version
4. **Compare**:
   ```
   - Total runtime: Δ%
   - Total cost: Δ%
   - Verification coverage: before → after
   - Claim count: before → after
   - Critical metrics (regulatory status, false positives)
   ```
5. **Report**: Document findings with recommendations

## Adding New Benchmark Companies

### Process

1. **Select**: Company must represent a distinct scenario (see Rationale column above)
2. **Obtain deck**: Real investor pitch deck; current as of recent quarter
3. **Manual review**: Domain expert reviews claims and identifies:
   - Material claims (those affecting investment decision)
   - Claim type (regulatory, trial, mechanism, etc.)
   - Ground truth from literature + regulatory databases
4. **Baseline**: Run through v1-beta, record metrics
5. **Create test**: `tests/integration/test_<company>_regression.py` with assertions on:
   - Claim count
   - Verification coverage
   - Regulatory status accuracy
   - At least one high-impact claim correctly identified
6. **Commit**: Add to regression suite with justification

### Example (CRISPR Therapeutics)

```python
async def test_crispr_base_editing_claims():
    """Base-edited program should be identified as early-stage."""
    # Assert: "NTLA-3001 in Phase 1 transfusion-dependent beta thalassemia"
    # Must be classified as Phase 1
    # Must retrieve ClinicalTrials.gov record confirming phase
    pass
```

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
