# BioIntel — Architecture

## 1. The problem

A biotech pitch deck is a compressed scientific argument. A VC needs to know
which of its claims are load-bearing, what evidence the company actually has
versus asserts, what the published literature says about the same target and
mechanism, and which questions would resolve the remaining uncertainty. Doing
that manually takes a scientific advisor a day per deck.

The hard part is not summarisation. It is **not being wrong**. A diligence tool
that invents a citation, or confidently reports a claim the deck never made,
is worse than no tool, because it consumes the trust it needs to be useful.
Every significant design decision below follows from that.

---

## 2. Shape of the system

```
┌──────────────┐      ┌───────────────┐      ┌──────────────────────┐
│  Next.js UI  │─────▶│  Route proxy  │─────▶│      FastAPI         │
│  (RSC + SSE) │      │ (API key kept │      │  documents / runs    │
└──────────────┘      │  server-side) │      │  claims / evidence   │
                      └───────────────┘      │  report / export     │
                                             └──────────┬───────────┘
                                                        │ enqueue
                                             ┌──────────▼───────────┐
                                             │   jobs table         │
                                             │  (durable queue)     │
                                             └──────────┬───────────┘
                                                        │ claim
                                             ┌──────────▼───────────┐
                                             │      Worker          │
                                             │  AnalysisPipeline    │
                                             └──────────┬───────────┘
                     ┌──────────────────────────────────┼───────────────────┐
                     ▼                  ▼               ▼                   ▼
              ┌────────────┐    ┌─────────────┐  ┌────────────┐    ┌────────────────┐
              │  PyMuPDF   │    │  LLM        │  │ PubMed /   │    │  PostgreSQL /  │
              │  parsing   │    │  provider   │  │ EuropePMC/ │    │  SQLite        │
              │  + render  │    │  (OpenAI |  │  │ CT.gov     │    │                │
              └────────────┘    │   offline)  │  └────────────┘    └────────────────┘
                                └─────────────┘
```

---

## 3. The pipeline

Ten stages, executed in order, each persisting its output before the next
begins. A failure late in the run still leaves the analyst with everything
produced up to that point.

| # | Stage | What it does | Failure mode |
|---|---|---|---|
| 1 | `parse` | Text layer, positioned blocks, tables, page classification, rasterisation of pages needing vision | **fatal** |
| 2 | `page_understanding` | Vision pass over scanned/graphical pages: transcription, chart values, figure descriptions | degrading |
| 3 | `profile` | Company, lead programme, indication, modality, pipeline, team, ask | degrading |
| 4 | `entities` | Diseases, targets, drugs, biomarkers, mechanisms, modalities, endpoints — model output reconciled with a deterministic gazetteer | degrading |
| 5 | `claims` | Claim extraction with verbatim quotes, then **quote verification**, deduplication, importance ranking | **fatal** |
| 6 | `retrieval` | Per-claim query planning, fan-out to three sources, cross-source dedupe, relevance + quality ranking | degrading |
| 7 | `adjudication` | Batched claim × evidence stance judgements with abstract-quote verification | degrading |
| 8 | `assessment` | Deterministic credibility scoring plus per-claim narrative verdicts | degrading |
| 9 | `questions` | Deterministic rule findings merged with model-generated risks and diligence questions | degrading |
| 10 | `report` | IC memo synthesis, citation resolution, Markdown/HTML rendering | degrading |

**Fatal vs degrading** is the key operational distinction. Without a parse or
without claims there is nothing to analyse, so the run fails. Everything else
degrades: a PubMed outage, one unreadable page, or a model timeout costs that
stage's contribution and is recorded as an explicit limitation on the memo —
it does not cost the analyst the report.

Progress is weighted per stage (`STAGE_WEIGHTS`) so the UI's progress bar
tracks real work rather than stage count; retrieval and adjudication together
are over a third of a run.

---

## 4. Anti-hallucination architecture

### 4.1 Structured outputs everywhere

Every model interaction is defined by a Pydantic model in
`app/llm/schemas.py`, compiled to a **strict** JSON schema
(`app/llm/json_schema.py`) and enforced at decode time. The converter handles
what OpenAI's strict dialect rejects: it forces `additionalProperties: false`,
makes every property required (optionality becomes a nullable `anyOf`),
flattens `allOf`, and folds dropped validation keywords (`minimum`,
`maxLength`, …) into the field description so the model still sees the
constraint. The response is then re-validated locally; a schema violation
triggers exactly one repair round-trip before failing.

### 4.2 Quote verification

This is the central control.

- **Claims.** The model must return the exact contiguous span its claim rests
  on. `app/utils/text.verify_quote` normalises PDF artefacts (ligatures, soft
  hyphens, smart quotes, non-breaking spaces) and checks the quote occurs in
  the page. Exact match, or fuzzy partial-ratio ≥ 0.88. A quote found on a
  *different* page is accepted but flagged. A quote found nowhere means the
  claim is **discarded**, and the count appears in the report's limitations.
- **Evidence.** Each adjudication must quote the abstract it was shown. An
  unverifiable quote **downgrades the stance to neutral** rather than being
  accepted — the record stays visible to the analyst but loses its ability to
  move the score.

The test suite includes the dangerous case explicitly: a fabricated quote using
the *same vocabulary* as the source but making a different assertion must be
rejected.

### 4.3 Citation integrity

The memo references claims as `[C#]` and evidence as `[E#]`, drawn from a
reference table built from real persisted rows. After generation every marker
is resolved; unresolvable ones are stripped from the text and reported as a
defect. The model is never in a position to invent a PMID, because it never
writes one — it writes a reference id that must already exist.

### 4.4 Deterministic scoring

The model contributes per-item judgements (stance, relevance, strength).
All aggregation is fixed arithmetic in `app/analysis/scoring.py`:

```
internal  = tier_weight(claimed_evidence_tier) × (0.55 + 0.45 × rigor)
            × hedging_penalty × quote_penalty

support   = Σ weighted_strength(supporting) + 0.5 × Σ weighted_strength(mixed)
contra    = (Σ weighted_strength(contradicting) + 0.5 × Σ mixed) × 1.35
net       = support − contra

external_raw        = 0.5 + 0.5 · tanh(net / 1.2)
evidence_confidence = 1 − exp(−(support + contra) / 1.0)
external            = 0.5 + (external_raw − 0.5) × evidence_confidence

credibility = 100 × (0.45 × internal + 0.55 × external)
```

Three properties this shape guarantees:

- **Absence of evidence is neutral.** With nothing retrieved,
  `evidence_confidence = 0`, so `external = 0.5` exactly. A claim is never
  punished for being novel; it is reported separately as uncorroborated.
- **Contradiction outweighs confirmation** by 1.35×, because disconfirmation
  is more informative than confirmation.
- **Study design caps model enthusiasm.** `weighted_strength` multiplies the
  model's strength by relevance, by a design weight (meta-analysis 1.00 →
  case report 0.25, retracted 0.0) and by a computed record-quality score.

The run-level score weights claims by category and importance, with
thesis-critical claims at 1.6×, then applies visible penalties for
contradicted or uncorroborated thesis-critical claims and for incomplete page
coverage. Every score ships with its full `breakdown`, so an IC can be shown
exactly why a claim scored 41 and not 72.

### 4.5 Deterministic rules

`app/analysis/rules.py` fires from computed facts, not model judgement:
translational gaps, effect sizes without statistics/controls/n, claims asserted
with no stated evidence, retracted records in the evidence set, terminated or
withdrawn trials at the same target, support drawn only from preprints,
pervasive hedging, low literature coverage. These findings **always** appear;
where a model-generated risk restates one, the rule wins because it carries an
auditable trigger. Rules that fire across many claims are aggregated into one
register entry so a medium-severity pattern is never crowded out.

---

## 5. Ingestion

`PyMuPDF` gives text, positioned blocks, tables, image coverage and vector
drawing counts in one pass. Each page is classified:

- `digital_text` — usable text layer, little imagery
- `mixed` — text plus significant imagery (both treatments)
- `scanned_image` — no usable text, image-covered → vision required
- `empty`

Only pages that would gain from it are rasterised (150 DPI, capped at 1600 px)
and sent to the vision model. **This is the largest cost lever in the
pipeline**: a 40-page text deck makes zero vision calls.

The vision stage produces a *composite page text* consumed by every downstream
stage, with each fragment labelled by origin — `[TABLE]`,
`[RECOVERED FROM IMAGE]`, `[FIGURE: bar_chart]`, `[CHART VALUES]`. A claim
quoting a recovered span is marked `from_visual`, and values estimated by
reading an axis are flagged as such and carry lower confidence.

---

## 6. Evidence retrieval

Three live sources, each contributing something the others cannot:

| Source | Contributes |
|---|---|
| **PubMed** (E-utilities) | MeSH indexing and publication types, which drive study-design scoring |
| **Europe PMC** | Preprints, European literature, citation counts |
| **ClinicalTrials.gov v2** | Whether anyone took this into humans, at what phase, and whether trials were terminated |

Shared plumbing (`app/evidence/http.py`) provides per-host rate limiting
(2 req/s to NCBI anonymously, 8 with a key), bounded exponential backoff that
honours `Retry-After`, and a database-backed response cache with a one-week
TTL. Two operational details learned from live traffic and encoded in the code:
NCBI returns 429 above ~2.5 req/s despite documenting 3, and ClinicalTrials.gov's
edge returns 403 to User-Agent strings that do not name a recognised client
library.

Per claim, the model plans queries — including at least one designed to surface
**disconfirming** evidence by targeting the question the claim would fail on,
not by negating it. Results are deduplicated across sources by DOI → PMID →
NCT (PubMed wins ties; Europe PMC citation counts and missing abstracts are
merged in), scored for relevance (0.6 × embedding cosine + 0.4 × lexical
token-set ratio) and quality, then diversified so a slot is reserved for each
source and near-duplicate titles are dropped.

Ranking happens **before** adjudication because adjudication is the expensive
step: the model should read the eight records most likely to settle the claim,
not the first eight PubMed returned.

---

## 7. Data model

Design decisions worth naming:

- **Everything is run-scoped.** Re-analysing a deck with a newer model or
  prompt version creates a new `AnalysisRun` and never destroys prior results.
  `AnalysisRun.config` snapshots the pipeline version, prompt version and model
  ids, so any result is reproducible and comparable.
- **`EvidenceItem` is deliberately global**, deduplicated by
  `(source, external_id)`. A publication is a fact about the world; the
  run-specific *interpretation* lives in `ClaimEvidenceLink`. This also means
  re-running a deck reuses records already fetched.
- **Provenance is first class.** `Claim` stores the quote, the page, the
  verification outcome and the match score — not just the extracted statement.
- **Enums are stored as strings via a converting `TypeDecorator`.** Native
  database enums make adding a vocabulary value a schema migration; plain
  strings hand application code untyped values. `EnumType` gives flexible
  storage and typed reads.

---

## 8. Jobs

A single `jobs` table with optimistic locking, rather than Celery or RQ.

The workload is a handful of long-running analyses, not millions of small
tasks. `UPDATE … WHERE status='queued' AND id=?` means two workers racing for
a row produce exactly one winner, on both PostgreSQL and SQLite. Workers
heartbeat while working; a job whose heartbeat goes stale is re-queued by any
worker's reaper, which is how a crashed worker recovers. Cancellation is a
flag the running pipeline polls between stages.

The decisive benefit: a job and the rows it writes share one transactional
store, so there is no window where a job is "done" but its results are not
visible.

`JOB_EXECUTION_MODE=inline` runs the pipeline in a tracked background thread
of the API process instead, for single-user local setups.

---

## 9. Degradation strategy

BioIntel must be runnable end to end without OpenAI credentials — for CI, for
demos, and so that a provider outage degrades rather than halts.

`StubProvider` produces schema-valid, content-grounded output from the
deterministic biomedical pattern library in `app/extraction/lexicon.py`:
gazetteers for diseases, targets, modalities, endpoints, biomarkers and model
systems; regexes for IC50/EC50/p-values/sample sizes/hazard ratios; and
heuristics for evidence tier, claim category, hedging and puffery. Its
embeddings are hashed bag-of-words — not semantically trained, but
deterministic and non-degenerate, so ranking code paths are exercised honestly.

This is *not* a mock. Integration tests assert on real pipeline behaviour, and
the same lexicon serves in production as a **backstop for entity recall**: a
gazetteer hit the model missed is added as a low-confidence candidate, because
silently dropping a target plainly written in the deck is the worse failure.

Degraded runs are labelled in `run.metrics`, in the API response, in the UI and
in the memo's own text.

---

## 10. Security

- **API keys** compared in constant time; auth may be disabled outside
  production, and configuration validation *refuses to boot* a production
  environment without keys or with the default secret.
- **Upload safety.** Size enforced *during* streaming, so an oversized body is
  rejected before it is buffered; magic-byte validation; encrypted and
  corrupt PDFs rejected; page-count ceiling. Filenames are sanitised for
  display, and storage paths are derived from the document id, never from user
  input. Render paths are resolved and checked to stay inside the render root.
- **Frontend proxy.** The browser never talks to the API directly. A Next.js
  route handler forwards with the API key attached server-side, and enforces an
  allowlist of reachable resources.
- **Markdown rendering.** Report bodies are model output, therefore untrusted.
  `src/lib/markdown.ts` escapes *everything* first, then re-introduces only the
  constructs the report prompt may use. Raw HTML cannot survive because `<` is
  escaped before any rule runs; only `http(s)` URLs are linkified.
- **Error handling.** Internal details never reach a client; a stable
  machine-readable `code` plus a safe message, with the detail in structured
  logs keyed by request id.
- Standard security headers, gzip, CORS restricted to configured origins,
  per-key and per-IP rate limiting on the API and a separate, stricter limit on
  uploads.

---

## 11. Observability

Structured logs (`structlog`) with `run_id`/`document_id`/`request_id` bound to
context, so a run's whole lifecycle is greppable. Every model call is written
to `llm_call_logs` with tokens, latency, attempts and estimated cost. Every
stage records duration and domain metrics (`claims`, `dropped_unverifiable`,
`by_stance`, `quote_verification_failures`, …) on `RunStage.metrics` and, in
aggregate, on `AnalysisRun.metrics` — exposed at `GET /runs/{id}/metrics`.

---

## 12. Testing strategy

- **Unit tests** cover the things that must not silently break: quote
  verification (including plausible-but-absent quotes), strict schema
  generation for every contract, PubMed XML parsing, cross-source dedupe,
  quality scoring, the full rule engine, and job-queue semantics including
  crash recovery.
- **Integration tests** drive the real orchestrator over a synthetic PDF that
  contains *deliberate flaws* — an effect size with no control, a clinical
  claim on mouse data, marketing superlatives, a hedged assertion — and assert
  those flaws are caught.
- **Network is blocked by default.** A unit test that reaches the network fails
  loudly; source clients are exercised through `httpx.MockTransport` so the
  full client stack is covered. Live-API tests are opt-in via
  `BIOINTEL_TEST_NETWORK=1`.
- **Migration drift** is checked in CI on both SQLite and PostgreSQL, because
  SQLite hides portability bugs.

---

## 13. Deliberate trade-offs

| Decision | Rationale | Cost |
|---|---|---|
| Synchronous ORM, async I/O | DB work is short; avoids the async-driver matrix and keeps ORM code simple | DB calls occupy a threadpool slot |
| DB-backed queue over Celery | One transactional store; no broker in the deployment | Polling latency; not suited to high-frequency tasks |
| Abstracts, not full texts | Full text is often paywalled and 30× the tokens | Abstracts can misrepresent a paper |
| Hand-mirrored TS types | A reviewed, stable frontend contract | Manual sync; caught by `typecheck` |
| Custom Markdown renderer | Untrusted model output must not reach the DOM as HTML | Supports only the constructs the prompt uses |
| SQLite default | The product runs on a laptop with zero setup | Postgres needed for concurrency |

---

## 14. What would come next

1. **Full-text retrieval** for open-access records (Europe PMC provides it),
   with section-aware adjudication.
2. **pgvector** for evidence embeddings, enabling cross-run semantic search
   over everything ever retrieved.
3. **Human-in-the-loop review**: analyst accept/reject on claims and
   adjudications, captured as labelled data.
4. **An evaluation harness**: a labelled corpus of decks with known ground
   truth, scoring extraction recall, adjudication accuracy and score
   calibration, run per prompt change.
5. **Multi-document diligence**: deck plus data room plus publications from
   the founders, cross-referenced.
6. **Patent and conference-abstract sources**, which is where a lot of early
   biotech evidence actually lives.
