# Retrieval

How BioIntel finds external evidence for a claim, ranks it, and decides what a
result — or an absence of one — is allowed to mean.

The design constraint that shapes everything here: **a failed search must not
look like a negative finding.** Retrieval that returns nothing has told us
about our search, not about the company. See [SCORING.md](SCORING.md) for how
that distinction is carried through to the score.

---

## 1. Sources

| Source | What it is authoritative for | Client |
|---|---|---|
| **openFDA** (Drugs@FDA) | U.S. approval status. Authoritative. | `app/evidence/openfda.py` |
| **ClinicalTrials.gov v2** | Trial existence, phase, status, enrolment, sponsor. Authoritative. | `app/evidence/clinicaltrials.py` |
| **PubMed** (E-utilities) | Peer-reviewed literature, publication types | `app/evidence/pubmed.py` |
| **Europe PMC** | Literature, including preprints and abstracts | `app/evidence/europepmc.py` |

The split matters. openFDA and ClinicalTrials.gov are **registries**: they are
the record. If a registry has no entry for a claimed approval, that is a real
adverse finding, because the registry is where it would be. PubMed and Europe
PMC are **literature**: plenty of true results are unpublished, so silence
there is weak evidence at most.

Every source is normalised onto one `EvidenceRecord` shape
(`app/evidence/models.py`), so ranking and grading are source-agnostic.

### Rate limits and identification

- NCBI E-utilities require a contact e-mail in the User-Agent
  (`ncbi_tool_email`). An `ncbi_api_key` raises the allowance.
- openFDA needs no key; a free key raises the limit from 240 to 1000 req/min
  (`openfda_api_key`).

---

## 2. Search strategy

Queries are generated per claim, not per document. A claim carries its own
entities — target, modality, indication, programme code — and those are what
make a query specific enough to be useful.

Query construction draws on:

- normalised entity aliases (`app/evidence/normalization.py`), which map
  company and target synonyms onto canonical forms, and expand concepts like
  "gene editing" into `CRISPR-Cas9`, `genome editing`, `base editing`,
  `prime editing`;
- the claim's own text;
- the claim type, which decides *which sources are worth asking at all* — a
  `REGULATORY_APPROVAL` claim goes to openFDA; a `CLINICAL_RESULT` goes to the
  literature and the registry.

Alias tables deliberately retain look-alike Unicode glyphs (Greek letters in
gene names), because that is what the literature actually uses; normalising
them away would defeat the table.

### Budgets

| Setting | Default | Meaning |
|---|---|---|
| `retrieval_page_size` | 25 | Max literature records fetched per generated query |
| `evidence_per_claim` | 8 | Max evidence items retained per claim after ranking |
| `retrieval_concurrency` | 4 | Simultaneous outbound requests |
| `retrieval_timeout_seconds` | 30.0 | Per-request timeout |
| `retrieval_max_retries` | 3 | Retries per request |
| `max_verification_claims` | 30 | Cap on claims sent for authoritative registry verification in one run |

Fetching 25 and keeping 8 is deliberate: ranking needs a pool wider than the
answer, but carrying 25 records per claim into reasoning would blow the
context budget without improving the top of the list.

---

## 3. Registry verification

Separate from literature retrieval, and stronger. `RegulatoryVerifier`
(`app/analysis/verification.py`) checks registry-verifiable claims directly
against openFDA and ClinicalTrials.gov, and returns one of a small set of
authoritative outcomes.

This is the path that produces a genuine adverse finding rather than a gap. A
worked example from the Moderna benchmark: Drugs@FDA does not index
CBER-licensed vaccines, so mRESVIA is legitimately absent from it. The
verifier must not read that absence as "the approval is fake" — which is
exactly the failure mode the benchmark pins.

Because verification is authoritative, its outcome overrides the literature
adjudication for that claim, and `authoritative=True` propagates into the
confidence model.

---

## 4. Ranking

Retrieved records are scored on two independent axes and combined.

**Relevance** — does this record concern this claim?

```
relevance = 0.6 × semantic_similarity + 0.4 × lexical_relevance
```

falling back to lexical alone when embeddings are unavailable, which keeps
retrieval working in degraded/offline mode.

**Quality** — how strong is this kind of evidence?

```
quality = quality_score(record)
```

derived from the evidence hierarchy below.

**Final rank:**

```
rank_score = 0.65 × relevance + 0.35 × quality
```

Relevance is weighted above quality on purpose: an excellent paper about a
different molecule is worse than useless, because it invites a false
corroboration.

### The evidence hierarchy

`EvidenceGrade`, strongest to weakest:

```
regulatory_approval
pivotal_trial_top_journal
pivotal_trial
meta_analysis
systematic_review
phase_2_trial
early_phase_trial
registry_with_results
registry_record
observational
preclinical
narrative_review
preprint
conference_abstract
company_statement
```

A regulatory approval is the strongest possible external corroboration of a
product claim. A company statement is at the bottom and is **not external
evidence at all** — it is the deck talking about itself, retained only so the
memo can show that this is all that was found.

Grading is in `app/evidence/grading.py` and keys off publication type, phase,
and whether a registry record has posted results.

---

## 5. Deduplication

The same paper reaches us from PubMed and Europe PMC under different
identifiers. `EvidenceRecord.dedupe_key` builds a cross-source identity key
(`source:external_id`, with DOI/PMID reconciliation) so one study is not
counted as two corroborations. Double-counting evidence is the cheapest
possible way to manufacture false confidence.

---

## 6. Caching

Literature responses are cached with a TTL of `retrieval_cache_ttl_hours`
(default **168 hours / 7 days**).

The TTL is a deliberate trade-off. Literature does not change hour to hour, so
a week-old PubMed response is fine and the cache makes re-runs of the same
deck fast and free. Registry checks are the case where staleness would matter
most — an approval can land any day — which is one more reason authoritative
verification is a separate path from cached literature retrieval.

Cache keys include the query, so a changed query is a changed key: prompt or
alias changes do not silently serve stale results.

---

## 7. Failure handling

Retrieval is a **non-critical stage**. Its failure degrades the run; it does
not abort it.

| Failure | Behaviour |
|---|---|
| One source times out | Other sources proceed; the error is recorded on `RetrievalResult.errors` |
| One claim's retrieval fails | Other claims proceed; that claim becomes unchecked, not unsupported |
| All retrieval fails | Run continues, `degraded=True`, a warning is attached, scores converge on the no-information anchor and confidence collapses |
| Network disabled entirely | Same as above; the offline deterministic analyser still produces a memo |

The critical property: **every one of these paths produces "we could not
check", never "the evidence disagrees".** A retrieval outage must not be able
to manufacture an adverse finding about a company. This is enforced by the
corroboration model — only `CONTRADICTED` and `IMPLAUSIBLE` may reduce a
credibility score, and neither is reachable from an empty or failed search.

Degradation is visible rather than silent: the run is flagged `degraded`, the
memo carries a degraded-mode banner, and `run_summary.md` says so.

---

## 8. Testing

Outbound HTTP is blocked in the test suite by default — a test that reaches
the network is a bug. Retrieval is exercised against `httpx.MockTransport`
with canned payloads, which means the full client stack still runs.

Live-source tests exist and are marked `network`; run them with:

```bash
make test-network
```

The benchmark suite pins one set of external responses per company
(`backend/tests/benchmarks/decks.py`). A benchmark is a deck *and* the world
it is checked against; both have to be fixed for the captured metrics to mean
anything.

---

## Related

- [SCORING.md](SCORING.md) — what a retrieval outcome does to a score
- [ARCHITECTURE.md](ARCHITECTURE.md) — where retrieval sits in the pipeline
- [CONFIGURATION.md](CONFIGURATION.md) — every retrieval setting
- [EVALUATION.md](EVALUATION.md) — retrieval-quality metrics in the baseline
