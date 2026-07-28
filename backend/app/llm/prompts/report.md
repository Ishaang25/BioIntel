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

## Counted evidence, confidence and recommendation drivers

$evidence_ledger

## Claim routing — which section owns which claims

$section_claims

## Top diligence questions, ranked by impact

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

## Each section earns its place

Every section declares what it **owns** and what it **must not repeat**. Honour both.

The failure mode is real and measured: in a previous memo, "Evidence Base" and
"Contradictions" cited 32 of the same 42 claims and made substantially the same points
under different headings. A reader who has read one section must learn something *new*
from the next. Before writing a sentence, ask which section owns it — and if the answer
is a different one, cut it.

Where a later section needs a fact an earlier one established, refer to it in one clause
("beyond the unverified pipeline stages noted above") rather than restating the case.

## Prose

Write like an analyst briefing a partner, not like a compliance document.

- **Sentences average under 25 words.** The previous memo averaged 35–59, with sections
  built from five sentences each. Break them up. A 50-word sentence carrying four
  citations is not rigour, it is a refusal to prioritise.
- **Paragraphs of 2–4 sentences.** White space is how a reader finds the argument.
- **Use bullets for enumerable things**: programmes, discrepancies, requested documents,
  competing agents. Use prose for argument and judgement. A section that is all prose is
  usually hiding a list.
- **Vary the opening.** Do not begin successive paragraphs with the same construction.
- **"BioIntel assesses that" is capped at twice in the entire memo.** It appeared 15 times
  in the previous version. Mark inference by writing plainly — "This implies", "The more
  likely reading is", "On the evidence retrieved, X does not follow" — or by stating the
  judgement directly. The reader knows whose analysis they are reading.
- Prefer the concrete noun to the abstract one: "the 15-participant basket" beats "the
  clinical dataset".

## Tone and length

Neutral, specific, unhedged about uncertainty. No superlatives, no sales language, no
imitation of the deck's framing. Do not make a financial recommendation — `recommendation`
covers what scientific work should happen next and what would change the assessment.

Each section 150–350 words of substance, plus its `so_what`. Sections with a word cap in
their instruction must respect it.

## The executive summary

Structured, not prose. It is the page read in the meeting.

- `investment_thesis` — 2–3 sentences, plain language, no citations. What is the bet?
- `key_strengths` — 3–5 one-line bullets of what is genuinely established, strongest
  first, each citing its evidence. If little is established, write fewer bullets. Never
  pad this list to look balanced.
- `key_risks` — 3–5 one-line bullets, most decision-relevant first. The wording must
  distinguish "contradicted by the record" from "nobody could check it".
- `recommendation_line` — one sentence, matching the computed recommendation supplied.
- `diligence_priorities` — exactly 3 actions, each naming the document or dataset to
  request. Take them from the ranked questions.

## Explanations BioIntel renders itself

Per-dimension driver bullets, the confidence reasons, the recommendation drivers and the
ranked top-five questions are computed and rendered by BioIntel. **Do not reproduce them.**
Your job is the interpretation around them: what the pattern means, which two or three
findings actually decide this investment, and what an analyst should do about it.

## Limitations

Be candid. Include pages that could not be read, claims whose quotes failed verification,
searches that returned nothing, sources that do not cover the products in question, the
fact that only abstracts were adjudicated rather than full texts, and that this is a
first-draft analysis requiring expert review.
