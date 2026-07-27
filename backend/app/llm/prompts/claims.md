Extract the claims a biotech company makes in these pitch-deck pages, and classify what
*kind* of assertion each one is.

A **claim** is a single assertion the company makes that a diligence team would want to
check: about a mechanism, a target, an experimental result, a clinical outcome, a
regulatory status, a safety property, a platform capability, or a competitive position.

## Source material

$pages

## Classifying the claim — the most important field you produce

`claim_type` decides how the claim is checked and how it is scored. Getting it wrong is
the most damaging error available to you, because a misclassified claim is checked
against the wrong source and scored by the wrong model.

**Statements of fact about the present world** (these are checked and scored):

| `claim_type` | Use when the deck asserts | Example |
|---|---|---|
| `regulatory_approval` | a product is approved, licensed or cleared | "mRESVIA has FDA approval for ages 60+" |
| `regulatory_submission` | a filing, submission, acceptance or PDUFA date | "Filed, PDUFA date May 30, 2025" |
| `clinical_result` | an outcome from a human study | "ORR of 42% in the Phase 2" |
| `pipeline_stage` | a programme is at a development stage | "CMV: Phase 3 efficacy" |
| `preclinical_result` | an animal or in vitro result | "62% reduction in the MPTP model" |
| `mechanism` | how the molecule acts | "allosteric LRRK2 kinase inhibition" |
| `biomarker` | a marker used for selection or response | "p-Rab10 is our engagement biomarker" |
| `safety` | tolerability or toxicity | "no adverse findings at 40x exposure" |
| `platform_capability` | what the platform can do in general | "our LNP delivers to hepatocytes" |
| `track_record` | the company's own historical performance | "62% Phase 1 POS vs 35% industry" |
| `ip_position` | patents or exclusivity | "composition-of-matter patent granted" |
| `partnership` | a named collaboration | "partnered with Vertex on VX-522" |
| `manufacturing` | CMC, scale, cost of goods | "COGS below $$2/dose at scale" |
| `competitive_position` | comparison to competitors or standard of care | "the only approved therapy in this setting" |

**Statements that cannot be true or false today** (reported, but excluded from scoring —
a company is not marked down for having a strategy):

| `claim_type` | Use when the deck states | Example |
|---|---|---|
| `market_estimate` | a market size or TAM | "$$14B opportunity" |
| `financial_guidance` | revenue, cost or margin projections | "we expect $$6B in 2027" |
| `forward_looking` | a plan or intention | "we will file an IND in Q3" |
| `strategic_objective` | a goal | "our objective is to lead in oncology" |
| `corporate_vision` | narrative or mission | "founded to use nature's information molecule" |
| `marketing` | unfalsifiable promotion | "revolutionary", "best-in-class platform" |

### The distinctions that are most often got wrong

- **"We will file an IND in Q3"** is `forward_looking`, not `regulatory_submission`. The
  submission type is for filings the company says have *already happened*.
- **"CMV: Phase 3 efficacy"** on a pipeline chart is `pipeline_stage`, not
  `clinical_result`. It asserts where the programme is, not what it showed.
- **"Four positive Phase 3 readouts"** is `clinical_result` — it asserts outcomes.
- **"Our platform's success rate exceeds industry"** is `track_record`, not
  `platform_capability`. It is a claim about the company's history, not about what the
  technology can do.
- **"First-in-class allosteric mechanism"** contains two claims: a `mechanism` claim and a
  `competitive_position` claim. Extract both.
- A superlative attached to a real claim does not make it `marketing`. "Revolutionary
  selectivity of 100-fold over 468 kinases" is a `mechanism` claim with promotional
  framing; set `hedging_language` false and let the number stand.

## Fields that need care

**`verbatim_quote`** — the exact contiguous span of text from the source above that carries
the claim. Copy it character for character, including typos. This is verified
automatically; a paraphrase will be rejected and the claim discarded.

**`page_number`** — the page the quote came from, matching the page it was listed under.

**`from_visual`** — true when the quote comes from a `[FIGURE]`, `[CHART]` or
`[RECOVERED FROM IMAGE]` block rather than the page's own text layer.

**`claimed_evidence_tier`** — the strongest evidence the *deck itself* offers for this
specific claim. Most non-experimental claim types legitimately state no tier: an approval
claim, a partnership or a pipeline stage does not describe an experiment, and
`none_stated` is the correct and expected answer for them. Do not treat that as a defect.
Reserve concern for a *result* claim that states no tier.

**`quantitative`** — every number attached to the claim, with units, comparator, n,
p-value and model system exactly as stated. Use `null` for anything the deck does not
state; a missing control arm is itself a signal, so do not fill it in.

**`hedging_language`** — true when the deck qualifies the claim ("may", "potential",
"designed to", "we believe").

**`is_thesis_critical`** — true when an investor's decision would materially change if
this claim were false. Typically the lead asset's mechanism, its key efficacy result, its
regulatory status, and its differentiating property. Expect roughly 3-8 per deck.

**`importance`** — how central the claim is to the scientific story, 0 to 1. Reserve
above 0.8 for the handful the pitch rests on.

**`confidence`** — how sure you are the claim was extracted and classified correctly.

## Output volume

Return the claims that matter — typically 15-40. Extract the promotional and
forward-looking statements too, correctly typed: knowing that a third of a deck is
unfalfisiable is itself a finding, and mislabelling them as scientific claims distorts the
assessment.
