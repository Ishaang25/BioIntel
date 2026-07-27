Write the scientific verdict on a single claim, and compare the evidence rather than
summarising it.

## The claim

$claim_statement

**As stated in the deck:** "$claim_quote"
**Kind of claim:** $claim_type
**Evidence tier the deck offers:** $claimed_tier
**Category:** $claim_category

## What would count as corroboration for this kind of claim

$corroboration_guidance

## Authoritative verification

$verification_summary

## Adjudicated evidence

Supporting: $supporting_count · Contradicting: $contradicting_count · Neutral: $neutral_count · Total retrieved: $evidence_count

$evidence_summary

## Choosing `corroboration_status`

This is the field that drives the score. The distinction between "we found nothing" and
"we found disagreement" is the single most important judgement in this analysis.

- **`corroborated`** — independent evidence of a grade capable of settling this kind of
  claim directly confirms it.
- **`partially_corroborated`** — the direction is confirmed but a material specific is not
  (the approval exists but not the stated population; the effect exists but not the stated
  magnitude).
- **`plausible_unverified`** — nothing directly on point, but the claim sits comfortably
  within what the retrieved literature establishes for this target, mechanism or class.
- **`insufficient_evidence`** — the search ran and returned nothing that bears on the
  claim. This says something about the search, **nothing** about the truth of the claim.
- **`not_independently_verified`** — the claim concerns something only a regulator, a
  registry or the company could confirm (a filing, a PDUFA date, an internal success rate,
  a patent), and that source was not available or does not cover it.
- **`contradicted`** — retrieved evidence genuinely disagrees. Reserve this for real
  incompatibility, never for a gap.
- **`disputed`** — the evidence points both ways with comparable weight.

**A claim must never be marked `contradicted` because nothing was found.** If you are
choosing between `insufficient_evidence` and `contradicted`, and no record actually
disagrees, the answer is `insufficient_evidence`.

## Comparative synthesis — do not summarise, compare

`comparisons` is where this analysis earns its place. For each substantive question the
records bear on, set out:

- **agreement** — what they concur on, and how firmly;
- **disagreement** — where they diverge, and *why*: different population, dose, endpoint,
  follow-up duration, or analysis population. If they do not genuinely disagree, leave
  this empty rather than inventing tension;
- **quality_contrast** — which record carries more evidential weight and why: design,
  randomisation, blinding, sample size, sponsorship, venue;
- **translatability** — what these records do and do not license a reader to conclude
  about *this company's* claim.

A paragraph that restates each abstract in turn is a failure of this task. The value is in
the relationships between the records.

## The rest

**`verdict`** — 2 to 4 sentences for an investment committee, keeping three registers
separable: what the company asserts, what the literature shows, and what BioIntel infers
from the gap. Begin inferential sentences with "BioIntel assesses" or "This implies".
Cite by reference id.

**`confidence` and `confidence_reason`** — high, medium or low, with a sentence on what
would raise it. Base this on the *evidence available*, not on how much you like the claim.
An unverifiable regulatory claim can carry low confidence and still be perfectly credible.

**`key_uncertainties`** — concrete and testable ("whether the 62% effect persists beyond
8 weeks"), never generic ("more research is needed").

**`novelty`** — 0 when the claim restates something well established, 1 when nothing
comparable was found. High novelty is ambiguous: it can mean genuine differentiation or
that the approach has been abandoned for a reason. Say which you think it is.

**`translational_gap`** — name the gap in one sentence when the evidence tier offered is
weaker than the claim implies; `null` when there is none.

**`so_what`** — one sentence on what this claim's status means for the decision. Not a
restatement of the verdict: its consequence. "This is the claim the thesis rests on and it
is currently uncheckable, so the data room request should lead with it" is a so-what.
"The claim is unverified" is not.

## Discipline

- Everything you assert about the literature must trace to the adjudicated records above.
- Do not recommend investing or not investing. Assess the science.
- One small negative study does not overturn a strong claim, and one supportive review
  does not establish one.
