Judge how each retrieved publication bears on a startup's claim.

## The claim being tested

$claim_statement

**Stated by the company as:** "$claim_quote"
**Category:** $claim_category
**Evidence tier the deck offers:** $claimed_tier

## Retrieved records

Each record below is real, retrieved from PubMed, Europe PMC or
ClinicalTrials.gov. Only the title, abstract and metadata shown are available to
you — you have not read the full text.

$evidence

## Your task

Return exactly one adjudication per record, in the order given, echoing each
record's `ref` exactly.

### Choosing a stance

- **`supports`** — the record reports a finding that makes the claim more likely
  to be true, in a system relevant to the claim.
- **`contradicts`** — the record reports a finding incompatible with the claim,
  or a failure of the same approach in a comparable or more rigorous setting.
- **`mixed`** — the record contains both supporting and undermining findings for
  this specific claim.
- **`neutral`** — the record is on-topic but does not settle the claim in either
  direction (background, methods, a review restating others' work).
- **`unrelated`** — the record concerns a different target, indication,
  population or question. Be willing to use this. Retrieval is keyword-driven
  and a large share of results are genuinely off-topic; forcing a stance on them
  is how a diligence tool becomes untrustworthy.

### Relevance and strength are different axes

- `relevance` — how closely the record addresses *this* claim: same target, same
  mechanism, same indication, same endpoint.
- `strength` — how decisively it settles the claim if relevant. Driven by study
  design (meta-analysis > RCT > single-arm trial > observational > preclinical >
  review), sample size, and directness of the measure. A perfectly on-topic
  mouse study bearing on a clinical claim has high relevance and low strength.

Both are low for `unrelated`. Do not use `strength` to express your enthusiasm
for the claim.

### The quote

`supporting_quote` must be a single sentence copied verbatim from the abstract
shown above. It is verified automatically against the abstract text; a
paraphrased or reconstructed sentence will cause the finding to be discarded.
Choose the sentence that most directly justifies your stance. Use an empty
string only when the stance is `unrelated`.

### Caveats

List the specific reasons this record does not fully settle the claim: species
difference, dose or exposure mismatch, surrogate rather than clinical endpoint,
small n, single centre, open label, different disease stage, industry-sponsored,
publication age, preprint status. Be concrete — "limitations apply" is useless.

### Discipline

- Judge only the record in front of you. Do not import what you know about the
  field, and do not let the plausibility of the claim influence the stance.
- A review article that merely restates a finding is weaker evidence than the
  primary study, and is usually `neutral`.
- A registry record with no posted results establishes that a trial exists, not
  that it worked. Treat "trial is recruiting" as `neutral` for an efficacy claim.
- If an abstract is truncated or absent, keep `strength` low and say so in
  `caveats`.
