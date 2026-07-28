# BioIntel Production Deployment Guide

## Pre-Deployment Checklist

### Environment & Infrastructure

- [ ] PostgreSQL database running (production-grade backups)
- [ ] Persistent storage mounted for uploads, renders, metrics
- [ ] Redis or similar for job queue (optional but recommended)
- [ ] Monitoring & alerting configured (Datadog, New Relic, etc.)
- [ ] SSL/TLS certificates installed (HTTPS only)
- [ ] Backup strategy defined (database + file storage)
- [ ] Log aggregation configured (ELK, Datadog, etc.)

### Application Configuration

- [ ] `.env` locked down with production secrets
- [ ] `OPENAI_API_KEY` set and key quotas verified
- [ ] `NCBI_API_KEY` obtained (raises PubMed rate limit)
- [ ] `OPENFDA_API_KEY` obtained (optional but recommended)
- [ ] `API_KEYS` generated and distributed to authorized clients
- [ ] `SECRET_KEY` changed from default
- [ ] `ENVIRONMENT=production` set
- [ ] `DATABASE_URL` points to production PostgreSQL
- [ ] `CORS_ORIGINS` restricted to your domain only
- [ ] `STORAGE_DIR` points to persistent shared storage

### LLM Configuration

- [ ] Model selection appropriate for your workload (see CONFIGURATION.md)
- [ ] Token budgets tuned for expected deck sizes
- [ ] Concurrency limits set appropriately for infrastructure
- [ ] Timeout values increased if on high-latency network
- [ ] Reasoning effort balanced for cost vs. quality

### Testing & Validation

- [ ] All tests pass: `make test`
- [ ] Lint passes: `make lint`
- [ ] Type checking passes: `make typecheck`
- [ ] Regression suite passes: `make regression`
- [ ] Sample deck runs end-to-end successfully
- [ ] Metrics are generated and readable

## Deployment Strategies

### Docker Deployment (Recommended for VC Firms)

```bash
# Build images
docker compose build

# Start services (API + Worker + PostgreSQL)
docker compose up -d

# Verify health
docker compose ps
docker compose logs api
```

**Compose stack includes:**
- API service (FastAPI on port 8000)
- Worker service (background job processor)
- PostgreSQL database (production-grade)
- pgAdmin (optional, for DB inspection)

### Kubernetes Deployment

Use Helm chart (create if deploying at scale):

```yaml
# values.yaml
api:
  replicas: 2
  resources:
    requests:
      memory: "2Gi"
      cpu: "1"

worker:
  replicas: 2
  resources:
    requests:
      memory: "4Gi"
      cpu: "2"

database:
  postgres:
    persistence:
      size: "100Gi"
```

### Traditional VPS/EC2 Deployment

1. **Create venv and install**:
   ```bash
   python -m venv /opt/biointel/.venv
   /opt/biointel/.venv/bin/pip install -e /opt/biointel/backend[dev]
   ```

2. **Run API with Gunicorn**:
   ```bash
   gunicorn -w 4 -b 0.0.0.0:8000 app.main:app
   ```

3. **Run worker(s)**:
   ```bash
   /opt/biointel/.venv/bin/python -m app.jobs.worker
   ```

4. **Reverse proxy** (nginx):
   ```nginx
   server {
       listen 443 ssl;
       server_name biointel.yourcompany.com;
       
       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

## High-Availability Setup

### Multi-Worker Configuration

For heavy load, run multiple workers on different machines:

```bash
# Machine 1: API server + 1 worker
docker compose up api worker

# Machine 2: Additional workers
worker_id=worker2 docker compose -f docker-compose.worker.yml up

# Machine 3: Database
docker compose up postgres
```

All connect to same PostgreSQL instance.

### Load Balancing

Use a load balancer (AWS ALB, nginx, HAProxy) to distribute API requests:

```
Client → Load Balancer → API-1, API-2, API-3
                      → Worker pool (2-4 workers)
                      → PostgreSQL (primary + replicas)
```

### Database Replication

For production, use PostgreSQL replicas:

1. **Primary**: Accepts writes, handles analysis jobs
2. **Read Replica(s)**: Serve report queries, metrics queries
3. **Backup**: Scheduled pg_dump to S3 hourly

### Disaster Recovery

- **RTO** (Recovery Time Objective): 4 hours
- **RPO** (Recovery Point Objective): 1 hour

Plan:
1. Database backups to S3 every hour
2. Docker image snapshots tagged with version
3. Stored configuration backed up to git (secrets excluded)
4. Documented runbook for restore from backup

## Monitoring & Observability

### Metrics to Track

**System Metrics:**
- API response time (p50, p95, p99)
- Worker job duration (p50, p95, p99)
- PostgreSQL query latency
- Disk usage (storage/uploads, storage/renders, storage/metrics)
- Memory usage (API process, worker processes)
- CPU usage
- Network I/O

**Application Metrics:**
- Analyses per day
- Success rate (% completed / started)
- Average cost per deck
- Average runtime per deck
- Errors by stage (parse, extraction, retrieval, etc.)
- LLM token usage (daily spend)

**Business Metrics:**
- Decks analyzed (weekly, monthly)
- Average deck size (pages)
- Average verification coverage
- User satisfaction (if user-facing)

### Alerting Rules

| Alert | Threshold | Action |
|-------|-----------|--------|
| API down | No responses for 5 min | Page on-call |
| Worker job queue depth | >100 jobs | Spin up additional workers |
| Database CPU | >80% for 10 min | Scale database, investigate slow queries |
| Cost per deck spike | >150% of baseline | Investigate prompt changes, model selection |
| Failure rate spike | >10% of runs | Check logs, API keys, database connectivity |
| Disk space | <10% free | Alert operations to cleanup/expand |

### Logging

All logs should include:

- Timestamp (ISO format)
- Log level (DEBUG, INFO, WARNING, ERROR)
- Run ID (for traceability)
- Service name (api, worker, etc.)
- Message
- Metrics (if applicable): tokens, duration, cost

**Example:**
```json
{
  "timestamp": "2026-07-28T14:32:15Z",
  "level": "INFO",
  "service": "worker",
  "run_id": "run_abc123",
  "event": "stage.done",
  "stage": "extraction",
  "duration_ms": 3450,
  "input_tokens": 8920,
  "output_tokens": 1240,
  "calls": 3
}
```

## Cost Optimization

### Token Usage

For a typical 25-page deck:
- **Input tokens**: 200k–300k (pages + prompts)
- **Output tokens**: 50k–100k (analysis results)
- **Cost**: $3–$6 (gpt-5-mini for extraction, gpt-5 for reasoning)

**Optimization strategies:**

1. **Use fast models for extraction**: gpt-4-turbo instead of gpt-5 saves 40%
2. **Cache prompts**: Identical pages → cached tokens (70% cheaper)
3. **Reduce reasoning effort**: "low" for extraction, saves 30%
4. **Batch decks**: Run analyses concurrently (better hardware utilization)
5. **Offline mode**: Run on decks with no LLM for 100% savings (limited accuracy)

### Caching

- Retrieval cache TTL: 168 hours (7 days) by default
- Same queries within cache window cost $0
- Monitor cache hit rate in metrics

### Rate Limits

- **PubMed**: 3 req/sec (10 req/sec with NCBI key)
- **openFDA**: 240 req/min (1000 req/min with key)
- **ClinicalTrials.gov**: 10 req/sec (no key needed)

Request API keys to avoid throttling during peak load.

## Security Considerations

### Data Privacy

- **Never log full PDFs**: Only log page metadata, claims, entities
- **Scrub PII from outputs**: Remove company contact info, email addresses
- **HIPAA/SOC2 readiness**: If handling real patient data, ensure compliance
- **GDPR**: If serving EU users, ensure data residency if required

### API Security

- **Authentication**: Use long random keys, rotate monthly
- **Rate limiting**: 60 req/min per key by default; tighten for public APIs
- **HTTPS only**: No HTTP traffic
- **CORS**: Restrict to your domain only
- **SQL injection**: SQLAlchemy ORM prevents this; no raw queries

### Secret Management

**Never commit secrets to git:**
- Use `.env.local` (git-ignored) for local development
- Use environment variables or a secrets manager (1Password, HashiCorp Vault) for production
- Rotate API keys quarterly

## Scaling Considerations

### Horizontal Scaling

As usage grows:

1. **API**: Add more instances behind load balancer (stateless)
2. **Workers**: Add more worker processes/machines (scales with queue depth)
3. **Database**: Use read replicas for queries; monitor write latency

### Vertical Scaling

If a single large deck is slow:

- **Increase concurrency**: `LLM_CONCURRENCY=16` (if infrastructure allows)
- **Increase model**: Use `gpt-5` instead of `gpt-4-turbo` for faster reasoning
- **Increase reasoning effort**: Trade speed for accuracy

### Bottleneck Identification

Check `storage/metrics/{run_id}/run_metrics.json` for slowest stage:

```json
{
  "stages": [
    {"stage": "extraction", "duration_ms": 45000},
    {"stage": "retrieval", "duration_ms": 120000},  // <-- slowest
    {"stage": "assessment", "duration_ms": 60000}
  ]
}
```

**If retrieval is slowest**: Increase `RETRIEVAL_CONCURRENCY` from 4 to 8
**If extraction is slowest**: Increase `LLM_CONCURRENCY` or reduce chunk size

## Rollback Procedure

If a version causes regressions:

1. **Identify**: Check regression test failures or metrics drop
2. **Rollback code**: `git checkout <previous-tag>`
3. **Redeploy**: `docker compose up --build`
4. **Verify**: Run regression suite again
5. **Notify**: Alert team and affected users
6. **Investigate**: Post-mortem on what broke

## Maintenance Windows

Schedule routine maintenance:

- **Database backups**: Every hour (automated)
- **Log rotation**: Daily (automated via Docker)
- **Cache clearing**: Weekly (cleanup old retrievals)
- **Security updates**: As released (patch OpenAI SDK, dependencies)
- **Secrets rotation**: Quarterly (API keys)

## Support Runbook

| Issue | Diagnosis | Fix |
|-------|-----------|-----|
| API not responding | Check logs: `docker compose logs api` | Restart: `docker compose restart api` |
| Jobs stuck in queue | Check worker logs; inspect DB | Restart workers, investigate blocking query |
| High CPU on one worker | Check which stage is running | Increase concurrency or add worker |
| PDFs not uploading | Check `STORAGE_DIR` permissions | Ensure write permissions: `chmod 755 storage/uploads` |
| High token spend | Check recent decks, model selections | Review prompts, consider cheaper model |
| Database slow | Check PostgreSQL logs | Analyze slow queries, add indexes |

---

**Contact**: engineering@biointel.example
