# Reporting

How BioIntel turns scores, claims and evidence into an investment memo — and
the constraints that stop that memo from saying anything the analysis did not
establish.

Two rules govern the whole layer:

1. **An explanation may never contradict the number it explains.** Anything
   that interprets a score is computed, not written by a model.
2. **Every section owns exactly one job.** Without that, adjacent sections
   converge and the memo restates itself.

---

## 1. Why section ownership exists

Measured on the CRISPR memo before this was enforced:

```
section                          words  bullets  sentences  avg sentence
Scientific Thesis                  295        0          5          59.0
Evidence Base and Its Limits       226        0          5          45.2
Contradictions and Unsupported     245        0          7          35.0

"Evidence Base" vs "Contradictions": 32 of 42 claims shared (Jaccard 0.76)
"BioIntel assesses that": 15 occurrences
```

Four sections restating the same findings, in 50-word sentences, with no
bullets. The fix is not a better prompt in general — it is giving each section
an explicit `owns` and an explicit set of things it must *not* discuss because
a later section owns them.

---

## 2. The section plan

`SECTION_PLAN` in `app/reporting/builder.py` is the single source of truth.
Nine sections, in order:

| # | Section | Owns |
|---|---|---|
| 1 | Scientific Thesis | What the company is asserting |
| 2 | Evidence Base and Its Limits | What data the deck itself puts on the table |
| 3 | External Literature Assessment | What independent science says about this biology |
| 4 | Open and Contested Claims | The specific claims an analyst must resolve |
| 5 | Translational Risk | The gap between the biology shown and the clinic implied |
| 6 | Competitive and Precedent Landscape | Who else has tried this, and what happened |
| 7 | Reading the Scorecard | What the computed numbers mean for the decision |
| 8 | Regulatory and Registry Verification | What authoritative sources returned |
| 9 | Recommended Diligence | What to do next, in order |

Each entry carries an `instruction` with explicit negative constraints. For
example, "Scientific Thesis" is told to state the company's argument and
then: *do not evaluate it here, do not cite external records, and do not
mention verification status — later sections do that.*

The ordering is an argument: what is claimed → what backs it internally →
what the world says → where they disagree → what that implies → what to do.

`report_sections` is an **exact-match** metric in the benchmark suite. A
section appearing or disappearing is a structural change, never noise.

---

## 3. What the model writes vs what BioIntel computes

| Written by the model | Computed by BioIntel |
|---|---|
| Section prose | Dimension narratives |
| Executive summary narrative | Confidence reasons |
| Competitive framing | Recommendation drivers |
| | Question ranking and "why it matters" |
| | Evidence ledger |
| | Scores, bands, recommendation |

The right column lives in `app/reporting/narrative.py` and is pure
computation over the scorecard. This is the mechanism behind rule 1: prose can
be wrong about tone, but it cannot assert a number the scorecard does not
hold, because it never generates the numbers.

Prefer deterministic logic over prompt engineering wherever possible — the
narrative module is the largest single application of that principle in the
codebase.

---

## 4. Driver bullets

Each dimension gets **positive** and **negative** driver bullets explaining
what moved it, generated from the claims that actually contributed weight.

One special case is worth knowing about. Dimensions computed from the deck's
own structure rather than from retrieved evidence — disclosure quality being
the clearest — take their drivers from their own rationale instead. Talking
about "corroborated claims" for a disclosure measure is a category error:
disclosure quality asks whether an analyst *could* check the deck, not what
checking it found.

When a dimension's verification coverage falls below the threshold in
`narrative.py`, coverage itself becomes the headline driver — the honest
statement is "we could not check this", not a manufactured finding.

Each dimension also carries a **what would move this** line: the concrete
thing that would change the score. That converts a static number into an
action.

---

## 5. Recommendation traceability

The recommendation never appears without its rationale. `_recommend`
(`app/analysis/scorecard.py`) returns `(recommendation, rationale)` as a pair
from every branch — there is no path that produces a verdict with no
explanation.

`recommendation_drivers` then expands that into the specific findings behind
it: which thesis-critical claims are contradicted, how much of the thesis they
represent, what coverage was, which company-reported figures need audit.

A reader can therefore go: recommendation → drivers → claims → evidence
records → source URLs, without leaving the memo.

---

## 6. Evidence traceability

Every claim carries provenance to the page and quote it came from, and quotes
are verified to actually occur in the document (`QuoteVerification`). A claim
whose quote cannot be found in the source is a defect, and the pipeline tests
assert this end to end.

Citations use `[C#]` for claims and `[E#]` for evidence records, resolved
through `ReferenceTable` (`app/reporting/builder.py`). Two properties matter:

- **Invalid citations are detected, not silently dropped.** A model-emitted
  reference that resolves to nothing lands in `invalid_citations` and is
  visible rather than quietly deleted.
- **The reference table is built from persisted records**, so a citation in
  the memo always points at a row that exists.

The **evidence ledger** summarises, per evidence state, how many claims sit
there — the memo's answer to "how much of this did you actually check?"

---

## 7. Question ranking

A list of fifteen good questions is not a diligence plan; it is a way of not
choosing. Questions are ranked by **expected impact on the decision**:

```
impact = priority_rank + best_claim_impact + small_breadth_bonus
```

`priority_rank`: critical 4.0, high 2.5, medium 1.0, low 0.5.

`best_claim_impact` is the **maximum** over linked claims, not the sum:

| Linked claim state | Thesis-critical | Otherwise |
|---|---|---|
| `CONTRADICTED` | 4.0 | 2.0 |
| `COMPANY_REPORTED` | 2.4 | 1.0 |
| `PLAUSIBLE_UNVERIFIED` | 2.0 | 0.8 |

Maximum rather than sum because impact is set by the most consequential thing
a question resolves. Summing per-claim bonuses would rank a broad question
about five settled claims above a narrow one about a single contradiction,
which is backwards. Breadth earns a small bonus but never enough to outrank
severity.

The analyst's own priority label is a strong prior, not the whole answer: a
"high" question that resolves a contradicted thesis claim outranks a
"critical" one attached to nothing in particular.

Each ranked question is returned with a computed `why_it_matters` string
("resolves a contradicted claim", "audits a company-reported figure",
"closes an unverified thesis claim").

---

## 8. Executive summary

The executive summary is written last, against the finished scorecard, and is
constrained to the recommendation and drivers already computed. It cannot
introduce a finding that no section supports.

It answers, in order: what is the company claiming, what did we establish,
how confident are we, and what should happen next.

---

## 9. Rendering

`render_markdown` (`app/reporting/renderer.py`) assembles the final document:
title, metadata, scorecard table, sections in plan order, references, and —
when applicable — the degraded-mode banner.

The rendered markdown is persisted on the `Report` row. Note that
`BuiltReport` holds *structured* sections; the rendered document is produced
by the renderer and its length is recorded by the report stage as
`markdown_chars`. (Reading `BuiltReport.markdown` is a mistake that has been
made before — it does not exist.)

Export formats are offered over the API; an unknown format is rejected rather
than silently defaulted.

### Degraded mode

When the run had no LLM provider, the memo carries an explicit banner saying
that chart reading, figure interpretation and semantic evidence adjudication
were not performed. A degraded memo must never be mistakable for a full one.

---

## 10. Report length as a signal

`report_length_chars` is tracked in the benchmark baseline with a 15%
tolerance. It is a blunt instrument, but it catches two real regressions
cheaply: a prompt change that makes sections balloon, and a truncation bug
that makes them vanish. Across the v1-beta benchmarks it sits in a tight band
of roughly 18.5k–20k characters.

---

## Related

- [SCORING.md](SCORING.md) — where the numbers the memo explains come from
- [RETRIEVAL.md](RETRIEVAL.md) — where cited evidence comes from
- [ARCHITECTURE.md](ARCHITECTURE.md) — anti-hallucination architecture in full
- [EVALUATION.md](EVALUATION.md) — report metrics in the baseline
