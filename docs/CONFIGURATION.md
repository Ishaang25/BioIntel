# BioIntel Configuration Guide

All configuration is sourced from environment variables. See `.env.example` for a complete annotated reference.

## Critical Settings

### LLM Configuration

| Variable | Default | Purpose | Impact |
|----------|---------|---------|--------|
| `LLM_PROVIDER` | openai | Language model backend | `stub` disables LLM (degraded mode); `openai` requires key |
| `OPENAI_API_KEY` | — | OpenAI authentication | **Required for full analysis** |
| `OPENAI_BASE_URL` | — | Custom OpenAI endpoint | Used for Azure OpenAI or proxies |
| `OPENAI_ORGANIZATION` | — | Organization ID | For multi-org accounts |

### Model Selection

| Variable | Default | Purpose | Tuning |
|----------|---------|---------|--------|
| `MODEL_REASONING` | gpt-5 | Deep analysis, claims, adjudication | Most expensive; increase for complex decks |
| `MODEL_FAST` | gpt-5-mini | Fast extraction, classification | Handles 80% of calls; optimal cost/speed |
| `MODEL_VISION` | gpt-5 | Chart/diagram reading | See `VISION_REASONING_EFFORT` to trade latency for quality |
| `MODEL_EXTRACTION` | gpt-5-mini | Entity extraction | Runs per-chunk; fast model is faster and cheaper |
| `MODEL_EMBEDDING` | text-embedding-3-small | Semantic search | Fixed to small; change only if performance suffers |

### Budget & Concurrency

| Variable | Default | Purpose | Note |
|----------|---------|---------|------|
| `LLM_MAX_CALLS_PER_RUN` | 400 | Hard ceiling on LLM calls | Cost guard; abort if exceeded |
| `LLM_MAX_INPUT_TOKENS` | 20,000 | Max input per call | Chunked extraction enforces this |
| `LLM_MAX_OUTPUT_TOKENS` | 32,000 | Max output per call | Reasoning + response headroom |
| `LLM_CONCURRENCY` | 12 | Parallel LLM call limit | Socket pool; increase for large decks |
| `LLM_TIMEOUT_SECONDS` | 180 | Per-call timeout | Increase if on slow network |

### Chunking & Extraction

| Variable | Default | Purpose | Note |
|----------|---------|---------|------|
| `EXTRACTION_CHUNK_MAX_PAGES` | 8 | Pages per extraction chunk | Tuned for claim + caveat locality |
| `EXTRACTION_CHUNK_INPUT_TOKENS` | 12,000 | Target chunk input size | Leaves room under 20k ceiling |
| `EXTRACTION_CHUNK_OUTPUT_TOKENS` | 6,000 | Output ceiling per chunk | Trigger split + retry if exceeded |
| `VISION_MAX_OUTPUT_TOKENS` | 6,000 | Page reading output ceiling | Per-page budget; prevents runaway |

### Retrieval

| Variable | Default | Purpose | Impact |
|----------|---------|---------|--------|
| `RETRIEVAL_ENABLED` | true | Enable external evidence retrieval | Set `false` for offline analysis |
| `RETRIEVAL_TIMEOUT_SECONDS` | 30 | Per-source timeout | PubMed/Europe PMC/trials.gov |
| `RETRIEVAL_PAGE_SIZE` | 25 | Results per query | Adjust down if hitting rate limits |
| `EVIDENCE_PER_CLAIM` | 8 | Max records retained per claim | Scoring uses top-ranked only |
| `RETRIEVAL_CACHE_TTL_HOURS` | 168 | External response cache TTL | 7 days; increase to reduce API calls |

### Verification

| Variable | Default | Purpose | Note |
|----------|---------|---------|------|
| `REGULATORY_VERIFICATION_ENABLED` | true | Check regulatory/pipeline claims | Requires openFDA + trials.gov access |
| `MAX_VERIFICATION_CLAIMS` | 30 | Claims sent for verification | Protect API rate limits |

### PubMed & NCBI

| Variable | Default | Purpose | Note |
|----------|---------|---------|------|
| `NCBI_API_KEY` | — | NCBI authentication | **Recommended**: Raises rate limit from 3 to 10 req/s |
| `NCBI_TOOL_EMAIL` | engineering@... | User-Agent email | Required by NCBI E-utilities |

### Database

| Variable | Default | Purpose | Note |
|----------|---------|---------|------|
| `DATABASE_URL` | sqlite at storage_dir | SQLAlchemy URL | Use PostgreSQL for shared deployments |
| `DB_POOL_SIZE` | 5 | Connection pool size | Increase for worker concurrency |
| `DB_MAX_OVERFLOW` | 10 | Pool overflow limit | Temporary connections above pool_size |

### Storage & Upload

| Variable | Default | Purpose | Note |
|----------|---------|---------|------|
| `STORAGE_DIR` | `./storage` | Root for documents/renders/metrics | Must be writable |
| `MAX_UPLOAD_MB` | 50 | Max PDF size | Increase for large presentations |
| `MAX_PDF_PAGES` | 400 | Max page count | Protect against pathological PDFs |

### Jobs & Workers

| Variable | Default | Purpose | Note |
|----------|---------|---------|------|
| `JOB_EXECUTION_MODE` | worker | Execution strategy | `inline` for tests/single-user; `worker` for production |
| `WORKER_CONCURRENCY` | 2 | Parallel analyses per worker | Increase if CPU allows |
| `JOB_MAX_ATTEMPTS` | 3 | Retry count on failure | Transient failures are reclaimed and retried |

### Security

| Variable | Default | Purpose | Note |
|----------|---------|---------|------|
| `API_KEYS` | — | Comma-separated auth tokens | **Required in production** |
| `SECRET_KEY` | dev-insecure | Session signing key | **Must change in production** |
| `CORS_ORIGINS` | localhost:3000 | Allowed origins | Comma-separated list |

### Reasoning Effort

| Variable | Default | Purpose | Trade-off |
|----------|---------|---------|-----------|
| `LLM_REASONING_EFFORT` | medium | Reasoning depth for complex tasks | low=faster, high=more accurate |
| `LLM_EXTRACTION_REASONING_EFFORT` | low | Effort for extraction (fixed task) | low is usually sufficient |
| `VISION_REASONING_EFFORT` | low | Effort for page reading | low=~57s/page; medium=slower but better chart reading |

## Tuning for Different Scenarios

### Fastest Turnaround (offline mode)

```bash
OPENAI_API_KEY=''              # Disables LLM (uses stub provider)
RETRIEVAL_ENABLED=false        # Skip literature search
LLM_CONCURRENCY=1              # Single worker
```

**Runtime:** 1–2 minutes per deck
**Cost:** $0.00
**Coverage:** Deterministic risk rules only

### Cost-Optimized (fast models, minimal retrieval)

```bash
MODEL_REASONING=gpt-4-turbo
MODEL_FAST=gpt-4-turbo
RETRIEVAL_PAGE_SIZE=10         # Fewer results
EVIDENCE_PER_CLAIM=4           # Fewer per claim
LLM_CONCURRENCY=4              # Balanced parallelism
```

**Runtime:** 5–8 minutes
**Cost:** ~$1.50–$3.00
**Coverage:** Full analysis, reduced scope

### High-Accuracy (deep reasoning, full retrieval)

```bash
MODEL_REASONING=gpt-5
LLM_REASONING_EFFORT=high
VISION_REASONING_EFFORT=medium
RETRIEVAL_PAGE_SIZE=25         # Max results
EVIDENCE_PER_CLAIM=8           # Max per claim
LLM_CONCURRENCY=12             # Full parallelism
```

**Runtime:** 10–15 minutes
**Cost:** $4.00–$8.00
**Coverage:** Maximum depth

### Production Deployment

```bash
ENVIRONMENT=production
DATABASE_URL=postgresql://user:pass@host/db  # PostgreSQL required
STORAGE_DIR=/data/biointel                   # Persistent storage
API_KEYS=<random-tokens>
SECRET_KEY=<strong-random-key>
OPENAI_API_KEY=<key>
NCBI_API_KEY=<key>                           # For rate limit increase
OPENFDA_API_KEY=<key>                        # For rate limit increase
JOB_EXECUTION_MODE=worker
WORKER_CONCURRENCY=4
```

## Environment-Specific Defaults

### Local Development

```bash
ENVIRONMENT=local
DATABASE_URL=sqlite:///./storage/biointel.db
JOB_EXECUTION_MODE=inline          # Synchronous for debugging
DEBUG=true
LOG_LEVEL=DEBUG
```

### Testing

```bash
ENVIRONMENT=test
RETRIEVAL_ENABLED=false            # Block network calls
JOB_EXECUTION_MODE=inline
LOG_LEVEL=WARNING
```

## Monitoring Configuration

Save metrics to: `storage/metrics/{run_id}/`

- **run_metrics.json**: Structured metrics (stages, tokens, cost)
- **run_summary.md**: Human-readable profile

These are generated automatically after each run and can be used for:
- Regression detection
- Cost tracking
- Performance profiling
- SLA monitoring

## Validation

Settings are validated at startup:

- Missing `OPENAI_API_KEY` → falls back to stub (degraded mode)
- `ENVIRONMENT=production` without `API_KEYS` → fails
- `ENVIRONMENT=production` with dev secret key → fails
- Invalid `DATABASE_URL` → fails with connection error
- Invalid paths (storage_dir, etc.) → auto-created if possible
