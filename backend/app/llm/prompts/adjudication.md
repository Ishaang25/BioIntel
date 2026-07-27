Judge how each retrieved publication bears on a startup's claim.

## The claim being tested

$claim_statement

**Stated by the company as:** "$claim_quote"
**Kind of claim:** $claim_type
**Category:** $claim_category
**Evidence tier the deck offers:** $claimed_tier

## What would actually corroborate this claim

$corroboration_guidance

## Retrieved records

Each record below is real, retrieved from PubMed, Europe PMC or ClinicalTrials.gov. Only
the title, abstract and metadata shown are available to you — you have not read the full
text.

$evidence

## Your task

Return exactly one adjudication per record, in the order given, echoing each record's
`ref` exactly.

### Choosing a stance

- **`supports`** — the record reports a finding that makes the claim more likely true, in
  a system relevant to the claim.
- **`contradicts`** — the record reports a finding **incompatible** with the claim, or a
  failure of the same approach in a comparable or more rigorous setting.
- **`mixed`** — the record contains both supporting and undermining findings for this
  specific claim.
- **`neutral`** — on-topic but does not settle the claim either way: background, methods,
  a review restating others' work, or a trial that exists without results.
- **`unrelated`** — a different target, indication, population or question. **Use this
  freely.** Retrieval is keyword-driven and a large share of results are genuinely
  off-topic; forcing a stance onto them is how a diligence tool becomes untrustworthy.

### `contradicts` is a high bar

This is the only stance that materially damages a company's assessment, so it must mean
what it says. Use it **only** when the record and the claim cannot both be true.

Do **not** use `contradicts` for:

- a record that simply does not mention the claim — that is `unrelated`;
- a record about the same disease but a different drug — that is `unrelated`;
- an absence of the specific result the company reports — absence of evidence is not
  contradiction;
- a study that is smaller, earlier or weaker than the company's — that is `neutral` with
  a caveat;
- a review that omits the company's finding — reviews are not exhaustive.

**Do** use `contradicts` when: the same intervention failed the same endpoint; a larger or
better-controlled study found no effect where the company reports one; the mechanism the
company proposes has been directly disproved; or a registry shows a different development
stage from the one asserted.

### `addresses_claim_directly`

True only when the record concerns the **same intervention** *and* the **same question**
as the claim. A paper on the same target but a different molecule is topically related,
not directly on point — set this false. This flag governs how much weight the record
carries, so an honest answer here matters more than a generous one.

### Relevance and strength are different axes

- `relevance` — how closely the record addresses *this* claim: same target, mechanism,
  indication, endpoint.
- `strength` — how decisively it would settle the claim if relevant, driven by study
  design, sample size and directness of the measure. A perfectly on-topic mouse study
  bearing on a clinical claim has **high relevance and low strength**.

Both are low for `unrelated`. Never use `strength` to express enthusiasm for the claim.

### The quote

`supporting_quote` must be a single sentence copied verbatim from the abstract above. It
is verified automatically against the abstract; a paraphrased or reconstructed sentence
causes the finding to be discarded. Use an empty string only when the stance is
`unrelated`.

### Caveats

State the specific reasons this record does not fully settle the claim: species, dose or
exposure mismatch, surrogate rather than clinical endpoint, small n, single centre, open
label, different disease stage, industry sponsorship, publication age, preprint status.
"Limitations apply" is useless.

### Discipline

- Judge only the record in front of you. Do not import background knowledge, and do not
  let the plausibility of the claim influence the stance.
- A review restating a finding is weaker than the primary study, and is usually `neutral`.
- A registry record with no posted results establishes that a trial exists and its design,
  not that it worked. For an efficacy claim that is `neutral`; for a **pipeline stage**
  claim it can be decisive.
- If an abstract is truncated or absent, keep `strength` low and say so in `caveats`.
