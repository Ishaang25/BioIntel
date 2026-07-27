Write the scientific due-diligence memo an investment committee will actually use.

## Company profile (from the deck)

$company_context

## Computed scorecard (by BioIntel, not by you — do not recompute any number)

$scorecard

## Scientific assessment

$scientific_assessment

## Claims, their corroboration status and adjudicated evidence

$claims

## Risk register

$risks

## Top diligence questions

$questions

## Section plan — follow it exactly, in order

$section_plan

---

# How to write this memo

## The standard

An IC memo is read by people deciding whether to commit capital and time. They have
already seen the deck. **A sentence that tells them what the deck says, without telling
them what it means, is wasted.** Every paragraph should survive the question: *so what?*

Weak: "The company reports a 62% Phase 1 probability of success versus 35% for industry."
Strong: "The 62% versus 35% Phase 1 success claim is the load-bearing platform argument
[C4], and it is self-reported with an undisclosed denominator. Until the programme list
and time window are provided, it cannot enter a portfolio model — and if it survives
scrutiny it is the single strongest argument for the valuation."

## Corroboration language — precision is mandatory

The analysis distinguishes states that were previously collapsed together. Preserve them:

- **Corroborated** — independent evidence confirms it.
- **Partially corroborated** — the direction is confirmed, a specific is not.
- **Plausible but unverified** — consistent with the literature, not individually confirmed.
- **Insufficient published evidence** — the search found nothing on point.
- **Not independently verified** — only a regulator, registry or the company could confirm it.
- **Contradicted** — evidence genuinely disagrees.

**Never write that a claim is "unsupported" when the finding is that it could not be
checked.** An FDA approval that BioIntel could not verify against a database is not a weak
claim; it is an unverified one, and the memo must say which. Conflating the two is the
specific failure this analysis exists to avoid.

Where a claim is uncheckable, say why in the same sentence — "regulators do not publish
pending submissions", "the FDA drug database does not index CBER-licensed vaccines" — so
the reader can judge whether the gap matters.

## Confidence

Each section carries a confidence level and a reason. Base it on the evidence behind the
section's conclusions, not on how positive they are. A section can conclude favourably
with low confidence, and unfavourably with high confidence; both are useful, and a memo
that never says "low confidence" is not being honest.

## Citations

Every factual sentence carries one of:

- `[C#]` — a claim the company makes;
- `[E#]` — an external record;
- an explicit inference marker: "BioIntel assesses that...", "This implies...".

A sentence with no citation and no inference marker is a defect. Never invent a reference
id. Use only ids that appear above.

## Numbers

Use the scorecard's figures exactly as given. Do not recompute, re-round, or invent one.
The scores were computed deterministically; your task is to explain what they mean, which
findings drove them, and what would move them.

## Registers — keep them separable

What the company claims → what the external evidence shows → what BioIntel infers. In that
order wherever both apply. Never blend them into one sentence.

## The most valuable content

1. **Contradictions.** Where evidence genuinely conflicts with a claim, give it its own
   paragraph, state the specific conflict, cite both sides, and say what it would take to
   resolve it.
2. **Uncheckable thesis-critical claims.** Say plainly which claims the thesis rests on
   that no public source can settle, and what document would settle each. This is usually
   the most actionable section of the memo.
3. **The gap between claimed and evidenced stage.** Where a deck's pipeline chart and the
   trial registry disagree, that is a finding, not a footnote.

## Tone and length

Neutral, specific, unhedged about uncertainty. No superlatives, no sales language, no
imitation of the deck's framing. Do not make a financial recommendation — `recommendation`
covers what scientific work should happen next and what would change the assessment.

Each section 150-400 words of substance, plus its `so_what`. Markdown: short paragraphs,
tables where they compress well, bullets only for genuinely enumerable items.

## Limitations

Be candid. Include pages that could not be read, claims whose quotes failed verification,
searches that returned nothing, sources that do not cover the products in question, the
fact that only abstracts were adjudicated rather than full texts, and that this is a
first-draft analysis requiring expert review.
