Write the scientific verdict on a single claim, given the evidence already adjudicated.

## The claim

$claim_statement

**As stated in the deck:** "$claim_quote"
**Evidence tier the deck offers:** $claimed_tier
**Category:** $claim_category

## Adjudicated evidence

Supporting: $supporting_count · Contradicting: $contradicting_count · Neutral: $neutral_count · Total retrieved: $evidence_count

$evidence_summary

## What to write

**`verdict`** — 2 to 4 sentences for an investment committee. It must make three
things separable:

1. what the company asserts,
2. what the retrieved literature actually shows,
3. what BioIntel infers from the gap between them.

Begin inferential sentences with an explicit marker such as "BioIntel assesses"
or "This implies". Never present an inference as a literature finding. Cite the
evidence you rely on by its reference id, e.g. `[E3]`.

If nothing was retrieved, say so directly: the claim is uncorroborated by
external literature, which is a statement about the search, not proof the claim
is false. Distinguish "no evidence found" from "evidence against".

**`key_uncertainties`** — the specific things that would have to be resolved
before an investor could rely on this claim. Be concrete and testable ("whether
the 62% effect persists beyond 8 weeks", "whether the effect holds in a
non-transgenic model"), not generic ("more research is needed").

**`novelty`** — 0 when the claim restates something well established in the
retrieved literature, 1 when nothing comparable was found. Note that high
novelty is ambiguous: it can mean genuine differentiation or it can mean the
approach has never been tried for a reason. Reflect that in the verdict where
relevant.

**`translational_gap`** — when the evidence tier the deck offers is weaker than
what the claim implies (a mouse result framed as a clinical prospect, an in
vitro potency framed as efficacy), name the gap in one sentence. `null` when
there is no material gap.

## Discipline

- Do not use background knowledge as evidence. Everything you assert about the
  literature must trace to the adjudicated records above.
- Do not recommend investing or not investing. Assess the science.
- Contradicting evidence deserves proportionate weight — one small negative
  study does not overturn a strong claim, and one supportive review does not
  establish one.
