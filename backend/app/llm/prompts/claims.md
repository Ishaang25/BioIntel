Extract the scientific claims a biotech company makes in these pitch-deck pages.

A **claim** is a single assertion the company makes about the world that could
in principle be checked: about a mechanism, a target, an experimental result, a
clinical outcome, a safety property, a biomarker, a platform capability, or a
regulatory status.

## Source material

$pages

## What counts as one claim

- One assertion per claim. "NG-101 inhibits LRRK2 with an IC50 of 3.2 nM and
  reduced neuron loss by 62% in the MPTP model" is **two** claims: a potency
  claim and an efficacy claim.
- Merge restatements of the same assertion across pages into the single
  strongest instance, quoting the page where it is stated most completely.
- Skip pure marketing ("we are a world-class team", "the market is $$40B") unless
  it makes a checkable scientific assertion. Set `is_scientific` false for
  commercial claims you do keep because they are entangled with a scientific
  one.
- Skip the company's forward-looking plans ("we will file an IND in 2026") —
  those are commitments, not claims about the world.

## Fields that need care

**`verbatim_quote`** — the exact contiguous span of text from the source above
that carries the claim. Copy it character for character, including any typos.
This is verified automatically against the document; a paraphrase here will be
rejected and the claim discarded. If the claim is stated across a heading and a
bullet, quote the bullet.

**`page_number`** — the page the quote came from. Must match the page the text
was listed under above.

**`from_visual`** — true when the quote comes from a `[FIGURE]`, `[CHART]` or
`[RECOVERED FROM IMAGE]` block rather than the page's own text layer.

**`claimed_evidence_tier`** — the strongest evidence the *deck itself* offers
for this specific claim, not what the field generally knows. If the deck says
"published data show X" without describing an experiment of its own, that is
`literature_only`. If the deck simply asserts X, that is `none_stated`. Do not
upgrade a tier because the claim sounds plausible.

**`quantitative`** — every number attached to the claim, with the units,
comparator, n, p-value and model system exactly as stated. Use `null` for
anything the deck does not state — a missing control arm or missing n is itself
an important signal, so do not fill it in.

**`hedging_language`** — true when the deck qualifies the claim ("may",
"potential", "designed to", "we believe"). Hedged claims often mark the boundary
of the actual data.

**`is_thesis_critical`** — true when an investor's decision would materially
change if this claim turned out to be false. Typically the mechanism of the lead
asset, its key efficacy result, and its differentiating property. Most claims are
not thesis-critical; expect roughly 3-8 per deck.

**`importance`** — how central the claim is to the company's scientific story,
0 to 1. Reserve values above 0.8 for the handful of claims the pitch rests on.

**`confidence`** — how sure you are that you extracted this claim correctly
(that the quote supports the statement and the categorisation is right). Lower
it when the source text is fragmentary, OCR-damaged, or ambiguous.

## Output volume

Return the claims that matter. A typical deck yields 15-40. Do not pad with
trivia, and do not omit a claim because it seems obviously true — obvious claims
are often the ones that fail diligence.
