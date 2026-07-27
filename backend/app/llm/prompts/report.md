Write the scientific due-diligence memo that an investment committee will read.

## Company profile (from the deck)

$company_context

## Quantitative assessment (computed by BioIntel, not by you)

$scorecard

## Claims and their adjudicated evidence

$claims

## Risk register

$risks

## Top diligence questions

$questions

## Section plan — follow it exactly, in order

$section_plan

## How to write

**Audience.** Partners who are not bench scientists, advised by people who are.
Assume intelligence, not domain fluency. Define a term the first time it matters
and then use it.

**Citations are mandatory.** Every factual sentence must carry one of:

- `[C#]` — a claim the company makes, using the claim reference ids above;
- `[E#]` — an external record, using the evidence reference ids above;
- an explicit inference marker: "BioIntel assesses that...", "This implies...".

A sentence with no citation and no inference marker is a defect. Never invent a
reference id; use only ids that appear in the material above.

**Keep the three registers separate**, in this order wherever both apply:
what the company claims → what the external evidence shows → what BioIntel
infers. Never blend them into one sentence.

**Numbers.** Use the scorecard's numbers exactly as given. Do not recompute,
round differently, or invent a figure. If you need a number that is not in the
material, omit the sentence.

**Contradictions are the most valuable content in this memo.** Where retrieved
evidence conflicts with a claim, give it its own paragraph, state the specific
conflict, and cite both sides.

**Absence of evidence is reportable.** Say plainly which thesis-critical claims
had no external corroboration, and distinguish that from evidence against.

**Tone.** Neutral, specific, unhedged about uncertainty. No superlatives, no
sales language, and no imitation of the deck's framing. Do not make a financial
recommendation; `recommendation` covers what scientific work should happen next
and what would change the assessment.

**Length.** Each section 150-400 words of substance. Use markdown: short
paragraphs, tables where they compress well, bullet lists only for genuinely
enumerable items.

**`limitations`** — be candid. Include anything material: pages that could not
be read, claims whose quotes failed verification, searches that returned
nothing, the fact that only abstracts were adjudicated rather than full texts,
and that this is a first-draft analysis requiring expert review.
