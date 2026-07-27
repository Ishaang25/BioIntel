Design literature searches that will test a specific claim made by a biotech startup.

## The claim

$claim_statement

**Category:** $claim_category
**Evidence tier the deck offers:** $claimed_tier
**Scientific entities involved:** $entity_names
**Company context:** $company_context

## What to produce

**PubMed queries (1-4).** These run against the live PubMed E-utilities API, so
they must be valid PubMed syntax.

- Use field tags where they sharpen the search: `[tiab]` for title/abstract,
  `[MeSH Terms]` for indexed concepts, `[pt]` for publication type.
- Combine concepts with `AND`; group synonyms with `OR` inside parentheses.
- Prefer the standard scientific name over the company's asset code — an
  internal code like "NG-101" will return nothing. Search the target, the
  mechanism and the indication instead.
- Keep each query under 25 words. Over-constrained queries return zero results,
  which is worse than a slightly broad one.

**At least one query must be designed to surface disconfirming evidence.** Set
its `intent` to `refute`. Do not do this by appending words like "negative" —
instead search the question the claim would fail on: prior clinical failures at
the same target, contradictory mechanistic findings, the same intervention in a
larger or better-controlled study, or known off-target liabilities.

**ClinicalTrials.gov terms.** Provide condition and intervention terms only when
the claim concerns something a trial registry would settle (a clinical result, a
development stage, a competitive landscape claim). Use plain terms, not boolean
syntax — this API takes simple terms. Leave the lists empty for a purely
preclinical or mechanistic claim.

## Worked example

Claim: "Our allosteric LRRK2 inhibitor slows dopaminergic neuron loss, supporting
disease modification in Parkinson's disease."

- `confirm`: `LRRK2[tiab] AND (kinase inhibitor[tiab] OR allosteric[tiab]) AND Parkinson Disease[MeSH Terms]`
- `refute`: `LRRK2 inhibitor[tiab] AND (adverse OR lung OR pneumocyte OR safety[tiab])`
- `background`: `LRRK2 G2019S[tiab] AND dopaminergic neuron[tiab]`
- conditions: `["Parkinson Disease"]`, interventions: `["LRRK2 inhibitor"]`

Note how the `refute` query targets the known pulmonary safety question for this
target class rather than simply negating the claim.
