# BioIntel

**Scientific due diligence for biotech venture investing.**

Upload a biotech pitch deck. BioIntel parses it — including scanned pages,
charts and tables — extracts every scientific claim with page-level
provenance, searches PubMed, Europe PMC and ClinicalTrials.gov for evidence,
adjudicates each claim against what it finds, scores its credibility, and
produces a cited Investment Committee memo.

The product's core promise is **traceability**: every claim carries the
verbatim quote it came from, verified against the document; every external
statement carries a real retrieved record; and every number in the scorecard
is computed by inspectable arithmetic rather than asserted by a model.

---

## Quick start

Requires Python 3.11+ and Node 20+. No database, Docker or API key needed to
start — though the analysis is much stronger with an OpenAI key.

```bash
git clone <this-repo> && cd BioIntel_v2
cp .env.example .env          # optional: add OPENAI_API_KEY
make setup
```

Then run the three processes, each in its own terminal:

```bash
make api        # http://127.0.0.1:8000  (docs at /docs)
```

```bash
make worker     # processes queued analyses
```

```bash
make web        # http://localhost:3000
```

Open <http://localhost:3000>, drop in a PDF, and watch the analysis run.

To try it without a deck of your own:

```bash
make seed
```

### Docker

```bash
docker compose up --build
```

Brings up PostgreSQL, the API, a worker and the web app, running migrations
first. The app is on <http://localhost:3000>.

---

## Running without an OpenAI key

BioIntel boots and runs a complete analysis with no credentials. It falls back
to a **deterministic offline analyser** built on a biomedical pattern library:
it still parses the PDF, extracts claims with verified quotes, retrieves real
literature from PubMed/Europe PMC/ClinicalTrials.gov, runs the full
deterministic risk-rule engine, scores credibility and produces a memo.

What it cannot do without a model: read charts and diagrams, interpret scanned
pages, judge evidence semantically, or write narrative analysis. Runs in this
mode are labelled **degraded** in the API, in the UI, and in the memo itself.

Set `OPENAI_API_KEY` in `.env` and re-run to get the full analysis.

---

## What an analysis produces

| Artefact | Description |
|---|---|
| **Claims** | Each with a verbatim quote, page number, category, the evidence tier the deck offers, extracted statistics, and linked entities. |
| **Entities** | Diseases, targets, drugs, biomarkers, mechanisms, modalities, endpoints, assays and model systems, deduplicated and ranked by salience. |
| **Evidence** | Real records from PubMed, Europe PMC and ClinicalTrials.gov, deduplicated across sources and ranked by relevance and study quality. |
| **Adjudications** | Per claim × record: supports / contradicts / mixed / neutral / unrelated, with a verified quote from the abstract and explicit caveats. |
| **Verification** | Regulatory and pipeline claims checked against openFDA and ClinicalTrials.gov, with confirmed / refuted / could-not-check reported separately. |
| **Scores** | Per-claim credibility 0–100 with a full component breakdown and a plain-language explanation, plus a ten-dimension IC scorecard and a scientific-diligence recommendation. |
| **Risks** | Deterministic rule findings (translational gaps, missing controls, terminated trials at the same target, retracted citations) merged with model-generated risks. |
| **Questions** | 8–15 specific technical questions for the company, each with a rationale and what a good answer contains. |
| **IC memo** | An eight-section cited memo, exportable as Markdown or standalone HTML. |

---

## How claims are scored

Scoring is the part of BioIntel most worth understanding, because the naive
version of it is actively misleading.

**Claim type decides the model.** A regulatory approval, a mouse result and a
revenue projection are epistemically different objects and do not share a
scoring model. Each type carries its own prior and its own sensitivity to
evidence. Statements that cannot be true or false today — guidance, plans,
corporate vision, marketing — are extracted, reported, and **excluded from
credibility scoring**. A company is not marked down for having a strategy.

**Absence of evidence is never evidence against.** The system distinguishes
seven outcomes of trying to check a claim:

| Status | Meaning | Score effect |
|---|---|---|
| Corroborated | independent evidence confirms it | strongly positive |
| Partly corroborated | direction confirmed, a specific is not | positive |
| Plausible, unverified | consistent with the literature, not confirmed | slightly positive |
| **No evidence found** | the search ran and returned nothing on point | **none** |
| **Not independently verified** | only a regulator or the company could confirm it | **none** |
| Disputed | evidence points both ways | negative |
| Contradicted | evidence genuinely disagrees | strongly negative |

Only the last two can reduce a score below its prior.

**Evidence is weighted by what it can establish.** Records are graded on an
explicit hierarchy — regulatory approval > pivotal trial in a top-tier journal
> meta-analysis > pivotal trial > Phase 2 > registry record > preclinical >
narrative review > preprint > conference abstract. Three narrative reviews
cannot corroborate a clinical result.

**Regulatory claims go to regulators, not to PubMed.** Approval, submission and
pipeline-stage claims are checked against openFDA and ClinicalTrials.gov, which
can actually settle them. A registry that positively disagrees with a deck's
stated phase is a real finding; a source that does not cover the product class
is a coverage gap, and the memo says which.

**The output is a scorecard, not a number.** Ten dimensions — scientific
validity, clinical maturity, regulatory confidence, evidence quality, execution
credibility, platform strength, pipeline diversification, translational
readiness, commercial readiness, disclosure quality — each with a confidence
band and the findings that drove it, weighted by company archetype. Dimensions
with no informing claims read as *not assessed*, never zero.

---

## Anti-hallucination controls

These are the mechanisms that make the output trustworthy, and they are
enforced in code rather than requested in a prompt:

1. **Quote verification.** Every claim's `verbatim_quote` is checked against
   the extracted page text (exact, then aggressive-normalisation fuzzy match at
   a ≥0.88 threshold). Claims whose quote cannot be located are **discarded**,
   and the count is reported as a limitation.
2. **Evidence quote verification.** Each adjudication must quote the abstract
   it was shown. An unverifiable quote **downgrades the stance to neutral**, so
   it cannot move the score.
3. **Citation resolution.** Every `[C#]`/`[E#]` marker in the memo is resolved
   against the real reference table. Unresolvable citations are stripped and
   recorded as a defect.
4. **Deterministic scoring.** The model supplies per-item judgements; all
   aggregation is fixed arithmetic with a published breakdown.
5. **Provenance discipline.** Outputs separate what the company claims, what
   external evidence shows, and what BioIntel infers.
6. **Structured outputs.** Every model call is constrained by a strict JSON
   schema at decode time and re-validated locally.
7. **Absence of evidence ≠ evidence of absence.** A claim with no retrieved
   literature scores neutrally on the external axis and is reported separately.

---

## Configuration

All configuration is environment variables; see [`.env.example`](.env.example)
for the annotated list. The ones that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Enables the full analysis. Without it, degraded mode. |
| `DATABASE_URL` | SQLite under `STORAGE_DIR` | Use PostgreSQL for anything shared. |
| `NCBI_API_KEY` | — | Raises the PubMed rate limit from 3 to 10 req/s. [Free.](https://www.ncbi.nlm.nih.gov/account/settings/) |
| `OPENFDA_API_KEY` | — | Raises the openFDA rate limit from 240 to 1000 req/min. [Free.](https://open.fda.gov/apis/authentication/) |
| `REGULATORY_VERIFICATION_ENABLED` | `true` | Check regulatory and pipeline claims against authoritative sources. |
| `JOB_EXECUTION_MODE` | `worker` | `inline` runs analyses in the API process (single user). |
| `API_KEYS` | — | Comma-separated keys. Empty disables auth; refused in production. |
| `LLM_MAX_CALLS_PER_RUN` | 400 | Hard cost ceiling per analysis. |
| `RETRIEVAL_ENABLED` | `true` | Set false to analyse without external calls. |
| `CORS_ORIGINS` | localhost | **Must list the frontend's origin when deployed** — see below. |

### Uploading large decks

The browser reaches the API through the frontend's server-side proxy, which
holds the API key. On a serverless host that proxy has a request-body ceiling
it does not control: a Vercel Serverless Function may receive at most **4.5 MB**
on every plan, and the request is rejected at the edge with `413` before any
application code runs. The API's own limit is `MAX_UPLOAD_MB` (50 by default).

Anything above the proxy's ceiling is therefore uploaded **straight to the API**.
The frontend's server exchanges its API key for a short-lived, single-use ticket
(`POST /documents/upload-ticket`) and the browser posts the file directly with
`X-Upload-Ticket`. See [`backend/app/core/upload_tickets.py`](backend/app/core/upload_tickets.py).

Two things this requires in a deployed environment:

- `CORS_ORIGINS` on the API must include the frontend's origin. The direct
  upload is cross-origin; without this it fails with an opaque browser error.
- The API must be reachable from the browser. It normally is — set
  `BIOINTEL_PUBLIC_API_URL` on the frontend only when the internal and external
  addresses differ.

If direct upload is unavailable, the product does not fail silently: it states
the largest file it can accept and asks for a compressed PDF.

A self-hosted `next start` has no such ceiling, so everything goes through the
proxy and neither setting is needed.

### Deployment shape: one API instance

The API runs as a single process and assumes it is the only one. Three things
depend on that, in descending order of how loudly they break:

1. Uploaded PDFs and page renders are written to the instance's local disk and
   addressed by absolute path, so a request routed to a second replica cannot
   find the document at all.
2. Rate-limit counters live in process memory (`app/api/deps.py`), so N replicas
   permit N times the configured limit.
3. Redeemed upload tickets are remembered in process memory
   (`app/core/upload_tickets.py`), so a replayed ticket landing on a different
   replica inside its 15-minute TTL would be accepted.

Scaling out means addressing all three — object storage for (1), a shared
counter for (2), and a `ReplayLedger` implementation for (3), which is a class
plus one `set_replay_ledger()` call. Fixing any one alone does not make the
system multi-replica safe.

---

## Testing

```bash
make test           # backend (pytest) + frontend (vitest)
make test-network   # additionally exercise the live literature APIs
make lint typecheck
```

The suite runs against a real SQLite database and the deterministic provider,
with **outbound HTTP blocked by default** — a unit test that reaches the
network fails loudly. Source clients are exercised through `httpx.MockTransport`
so the full client stack is covered without a live dependency.

---

## Repository layout

```
backend/
  app/
    core/         config, logging, errors, domain vocabularies
    db/           SQLAlchemy models, session, portable column types
    ingestion/    PDF parsing, page classification, rasterisation
    llm/          provider abstraction, strict schemas, prompts, offline analyser
    extraction/   page understanding, entities, claims, biomedical lexicon
    evidence/     PubMed, Europe PMC, ClinicalTrials.gov, ranking
    analysis/     adjudication, scoring, deterministic rules, questions
    reporting/    memo assembly, Markdown/HTML rendering
    pipeline/     the ten-stage orchestrator
    jobs/         durable queue and worker
    api/          FastAPI routers, auth, rate limiting
    services/     document and run lifecycle
  alembic/        migrations
  tests/          unit + integration
frontend/
  src/app/        Next.js App Router pages and the API proxy
  src/components/ upload, progress, claim explorer, report view
  src/lib/        typed API client, safe Markdown renderer
```

## Documentation

| Document | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System shape, pipeline stages, anti-hallucination architecture, data model, degradation |
| [docs/SCORING.md](docs/SCORING.md) | The ten dimensions, evidence states, aggregation, confidence, recommendation logic |
| [docs/RETRIEVAL.md](docs/RETRIEVAL.md) | Sources, search strategy, ranking, registry verification, caching, failure handling |
| [docs/REPORTING.md](docs/REPORTING.md) | Section ownership, traceability, driver bullets, question ranking, rendering |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Benchmark suite, the v1-beta baseline, regression policy, how to compare versions |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every configurable parameter |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Running BioIntel in a real environment |
| [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) | Risks, gaps and the roadmap |

---

## Limitations

BioIntel produces a **first draft** for a scientific advisor to review, not a
finished investment opinion. In particular:

- Adjudication reads **abstracts**, not full texts; a paper's real content can
  differ from its abstract.
- Retrieval is keyword-driven. A claim about proprietary unpublished work will
  correctly return nothing, and that is reported as absence of evidence.
- Values read from charts carry reading error and are flagged as estimated.
- The corpus is PubMed, Europe PMC, ClinicalTrials.gov and openFDA: no
  patents, no conference abstracts, no EMA database, no non-US/EU registries.
- openFDA's Drugs@FDA dataset does not index CBER-licensed vaccines, so those
  approvals resolve to *not independently verified* rather than confirmed.
- Scoring priors encode a defensible view of how different kinds of claim
  should be treated before evidence, but they are a view. They live in one
  readable table (`app/analysis/claim_policy.py`) precisely so they can be
  argued with.
- Scoring weights encode a defensible view of evidence hierarchy, but they are
  a view. The full breakdown is exposed so it can be argued with.

---

## Licence

Proprietary. All rights reserved.
