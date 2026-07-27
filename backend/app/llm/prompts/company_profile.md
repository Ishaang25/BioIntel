Build a factual profile of the company from its pitch deck.

## Source material

$pages

## Rules

- Every field must be sourced from the deck. Use `null` (or an empty list) for
  anything the deck does not state. Do not use outside knowledge about the
  company, even if you recognise it — an investor needs to know what the deck
  claims, not what the internet says.
- `one_liner` — the company's own description of itself, quoted or minimally
  condensed from the deck.
- `development_stage` — the furthest stage the deck claims for the lead
  programme, using the deck's own words (e.g. "IND-enabling", "Phase 1b"). If
  the deck shows a pipeline chart, read the stage from the chart.
- `pipeline` — one entry per programme shown, including preclinical and
  discovery-stage assets. Leave any field the deck does not state as `null`.
- `team` — only people the deck actually names. Record credentials as printed;
  do not expand or verify them.
- `total_raised`, `current_raise`, `use_of_funds` — as stated, verbatim in
  substance (e.g. "$$25M Series A").
- `ip_position` — what the deck claims about patents or exclusivity. Do not
  assess the claim here.
- `source_pages` — pages that informed this profile.
