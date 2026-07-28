# Scoring

How BioIntel turns a deck full of claims into ten dimension scores, one
overall number, a confidence, and a recommendation — and, more importantly,
what each of those is *allowed* to mean.

The governing rule, from which most of the design follows:

> Absence of evidence is not evidence of absence.

A claim nobody has published is not a weak claim. It is an unchecked one.
Confusing the two is the bug that scored Moderna — a company with two
FDA-approved products — at 24.1/100 "unsupported". Everything below exists to
keep those two situations apart.

---

## 1. The two axes

Two questions get asked about every claim, and they are independent:

| | Question | Answer |
|---|---|---|
| **Credibility** | How much should a reader believe this? | A score |
| **Confidence** | How much should a reader believe *our score*? | A separate score |

A claim with no external record gets a moderate credibility (it is not
disbelieved) and a low confidence (we could not check it). A claim the
registry contradicts gets a low credibility and a *high* confidence — we know
exactly what we found.

Collapsing these into one number is what makes an unverified deck look like a
bad one. They are reported separately end to end, and the recommendation
routes on both.

---

## 2. Claim types decide how a claim can be checked

`app/analysis/claim_policy.py` assigns every claim a policy from its
`ClaimType`. The policy carries a `VerifiabilityClass`, which decides what a
null retrieval result *means*:

| Verifiability | Example claim type | A null result means |
|---|---|---|
| `REGISTRY_VERIFIABLE` | `REGULATORY_APPROVAL`, `PIPELINE_STAGE` | Genuinely adverse — the registry is authoritative and the record is absent |
| `LITERATURE_VERIFIABLE` | `CLINICAL_RESULT`, `MECHANISM` | Weakly informative — plenty of real results are unpublished |
| `COMPANY_INTERNAL` | `PRECLINICAL_RESULT`, `TRACK_RECORD` | Uninformative — requires audit, not scepticism |
| `NOT_VERIFIABLE` | `FORWARD_LOOKING`, `MARKETING`, `CORPORATE_VISION` | Nothing; excluded from scoring entirely |

This is why "our Phase 1 success rate is 62%" does not damage a score. It is
`TRACK_RECORD` → `COMPANY_INTERNAL`: unauditable from outside, so it is
flagged for audit rather than counted against the company. And it is why
"mRESVIA is FDA approved" *can* damage a score — that is registry-checkable,
so silence there is a real finding.

Promotional and forward-looking statements are **excluded, not penalised**.
A deck is not worse for containing a vision slide.

---

## 3. Evidence states

Corroboration outcomes collapse into one vocabulary the scorecard reasons in
(`app/analysis/evidence_state.py`). Each state carries an *informativeness*
weight — how much this claim licenses any conclusion at all:

| Evidence state | Informativeness | Meaning |
|---|---|---|
| `VERIFIED` | 1.00 | External evidence confirms it |
| `CONTRADICTED` | 1.00 | External evidence genuinely disagrees |
| `IMPLAUSIBLE` | 1.00 | Inconsistent with established biology |
| `PARTIALLY_VERIFIED` | 0.70 | Confirms part of it |
| `PLAUSIBLE_UNVERIFIED` | 0.22 | Nothing for, nothing against |
| `COMPANY_REPORTED` | 0.15 | Rests on data only the company holds |
| `NOT_APPLICABLE` | 0.00 | Not a factual claim about the present world |

Note that `CONTRADICTED` is as informative as `VERIFIED`. Finding out that
something is false is just as much of a finding as confirming it. Only
`IMPLAUSIBLE` earns a strong negative penalty on its own.

### The no-information anchor

When nothing informative bears on a dimension, its score is **58**, not 0 and
not 50.

Zero would say "we established this is bad". Fifty would say "average". Both
are claims we have not earned. 58 is the bottom of the "promising but
unproven" calibration band, which is precisely what an unverified,
uncontradicted scientific claim is. The number sits where a reader will not
mistake it for a finding.

---

## 4. The ten dimensions

| Dimension | The question it answers |
|---|---|
| Scientific validity | Is the underlying biology sound and consistent with published science? |
| Clinical maturity | How far into human testing has this actually progressed? |
| Regulatory confidence | How much regulatory de-risking is real and verifiable? |
| Evidence quality | How strong is the evidence behind the claims, by study design? |
| Execution credibility | Has this team delivered what it said it would? |
| Platform strength | Does the platform generalise, or is it one asset with a story? |
| Pipeline diversification | How concentrated is the risk in a single programme? |
| Translational readiness | How large is the gap between the evidence shown and the claim implied? |
| Commercial readiness | How close is this to a product a payer will reimburse? |
| Disclosure quality | Does the deck disclose enough to be checked, or does it assert? |

Ten dimensions rather than one number because the failure modes are
different. A company can be scientifically excellent and commercially
hopeless; a single score cannot say that, and an investment committee needs
to know which axis the problem is on.

A dimension that no claim informs is reported as **not assessed** rather than
scored. `assessed` and `applicable` are distinct flags: a non-biomedical
company's scientific dimensions are inapplicable, not merely unmeasured.

---

## 5. Aggregation within a dimension

Each claim contributes to a dimension with weight:

```
weight = policy_dimension_weight × (0.4 + 0.6 × importance)
```

The importance term stops a peripheral claim dominating a dimension it barely
touches. The dimension score is then an **informed aggregate**: each claim is
weighted by its evidence-state informativeness, and whatever weight is left
over goes to the no-information anchor rather than being silently
redistributed.

This is the fix for the compounding bug. Previously a plain weighted mean of
credibility meant one unverified claim informing five dimensions was charged
against all five, so a single gap was counted five times over. Now an
uninformative claim contributes almost nothing to the score and instead pulls
the dimension's **information ratio** down, which is a confidence signal.

Measured before that change, across the benchmark decks:

```
Moderna    23 claims insufficient_evidence,      mean credibility 40.8
Beam       37 claims not_independently_verified, mean credibility 31.1
Recursion  26 claims plausible_unverified,       mean credibility 46.1
```

Nothing contradicted any of them.

---

## 6. Archetype routing and weights

A platform company's thesis is the platform's generalisability. A single-asset
company's thesis is one molecule. Scoring them identically over-penalises the
first for thin per-asset evidence and under-penalises the second for
concentration risk.

So dimension weights depend on the detected `CompanyArchetype`
(`ARCHETYPE_WEIGHTS` in `app/analysis/scorecard.py`). The highest and lowest
weighted dimensions per archetype:

| Archetype | Weighted up | Weighted down |
|---|---|---|
| Platform | Platform strength (1.3) | Commercial readiness (0.5) |
| Commercial stage | Regulatory confidence (1.3), Commercial readiness (1.2) | Translational risk (0.6) |
| Clinical-stage asset | Clinical maturity (1.3), Evidence quality (1.2) | Platform strength (0.5) |
| Preclinical asset | Scientific validity (1.3), Translational risk (1.3) | Commercial readiness (0.3) |

`TOOLS_AND_SERVICES` reuses the platform weights; `DIAGNOSTICS`,
`MEDICAL_DEVICE` and `UNKNOWN` reuse clinical-stage-asset. Non-biomedical
archetypes score only the archetype-neutral axes — execution, commercial
readiness, disclosure, diversification, evidence quality — and the biomedical
axes are reported inapplicable rather than scored badly.

The overall score is the weighted mean over **assessed** dimensions only:

```
overall = Σ(score × weight) / Σ(weight)   for assessed dimensions
```

An unassessed dimension does not drag the average toward zero.

---

## 7. Confidence

Confidence is computed from what we were able to check, not from what we
found. Its inputs:

- **Verification coverage** — share of scorable claims with a real external
  outcome (corroborated or contradicted; unchecked does not count).
- **Information ratio** — share of each dimension's claim weight that carried
  real information.
- **Per-claim confidence** — weighted by the same contribution weights used
  for the score.
- **Authoritativeness** — whether a regulator or registry settled the question.

Confidence bands: `high` ≥ 0.70, `medium` ≥ 0.40, `low` below that.

### Confidence is not credibility

This distinction is load-bearing enough to restate:

|  | Credibility | Confidence |
|---|---|---|
| Question | Is the claim true? | Do we know? |
| Contradicted claim | Low | **High** |
| Unverified claim | Moderate | **Low** |
| Corroborated by FDA | High | High |

A report can legitimately say "strong science, low confidence". That is not a
hedge; it is the most useful thing the analysis can tell an investor, because
it names diligence work rather than a verdict.

---

## 8. Recommendation

The recommendation routes on the science, our certainty, *and* coverage —
deliberately not on the score alone. Keying off one number conflates two
situations an IC treats completely differently:

```
strong biology, little external verification  ->  advance, with conditions
strong verification, weak or adverse biology  ->  significant concerns
```

Both produce a similar middling number. Routing on score alone sent the first
to "significant concerns", which is the opposite of correct: the response to
an information gap is diligence, not rejection.

The ladder, in order (`_recommend` in `app/analysis/scorecard.py`):

1. **No dimension assessable** → `FURTHER_DILIGENCE_REQUIRED`.
2. **Any `IMPLAUSIBLE` claim** → `DO_NOT_ADVANCE`. A scientific objection, not
   an evidence gap.
3. **Contradicted thesis-critical claims** → proportionate, not absolute.
   Decisive (`SIGNIFICANT_CONCERNS`) if there are ≥2, or they are >34% of
   thesis-critical claims, or overall < 58. Otherwise
   `ADVANCE_WITH_CONDITIONS` with the discrepancy as a gating condition. One
   overstated development stage at a company with approved products is a
   condition to clear; the same finding with nothing else verified is
   decisive.
4. **Non-biomedical archetype** → `FURTHER_DILIGENCE_REQUIRED`, routed to
   commercial diligence.
5. **Strong science (≥70) + coverage ≥40% + confidence ≥0.6 + no weak
   dimension** → `ADVANCE`.
6. **Strong science, thin verification** → `ADVANCE_WITH_CONDITIONS`. The
   constraint is what we could check, not what we found.
7. Below that, `FURTHER_DILIGENCE_REQUIRED` / `SIGNIFICANT_CONCERNS` by
   soundness (≥58) and weak-dimension count.

A dimension counts as **weak** only at score < 40 *and* information ratio
≥ 0.35 — i.e. only when we actually established weakness rather than failing
to look.

Every branch returns a rationale string alongside the recommendation, so the
memo never states a recommendation it cannot explain.

---

## 9. How scores move

Useful intuitions for reading a diff between two runs:

| Change | Effect |
|---|---|
| A claim moves `PLAUSIBLE_UNVERIFIED` → `VERIFIED` | Dimension rises toward that claim's credibility; confidence rises sharply (informativeness 0.22 → 1.00) |
| A claim moves `PLAUSIBLE_UNVERIFIED` → `CONTRADICTED` | Dimension falls; confidence *also* rises |
| Retrieval fails entirely | Scores converge on 58; confidence collapses; recommendation moves to a diligence branch, not a negative one |
| More marketing slides | No effect — excluded, not penalised |
| Archetype misdetected | Weights shift; this is the highest-leverage single input, which is why it is reported explicitly in the memo |

The rule to hold onto: **only `CONTRADICTED` and `IMPLAUSIBLE` may reduce a
credibility score.** Anything else that looks like a fall is either an
exclusion or a confidence effect.

---

## 10. Calibration bands

Thresholds are in `CREDIBILITY_BANDS` (`app/analysis/scoring.py`):

| Band | Range | Reading |
|---|---|---|
| `STRONG` | ≥ 75 | Independently corroborated across dimensions |
| `MODERATE` | 60–74 | Sound, with identified gaps |
| `LIMITED` | 45–59 | Promising but substantially unproven |
| `WEAK` | 30–44 | Material concerns established |
| `UNSUPPORTED` | < 30 | Claims contradicted or absent where they should exist |

Note that the no-information anchor (58) sits in `LIMITED`, near its top —
an unchecked company reads as "promising but unproven", never as "weak".

The v1-beta benchmark scores sit between 60.66 (Beam) and 81.47 (Recursion);
see [EVALUATION.md](EVALUATION.md) for the full baseline and the expected
range per company.

---

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — where scoring sits in the pipeline
- [RETRIEVAL.md](RETRIEVAL.md) — where the evidence comes from
- [REPORTING.md](REPORTING.md) — how scores become memo prose
- [EVALUATION.md](EVALUATION.md) — the benchmark baseline and regression policy
