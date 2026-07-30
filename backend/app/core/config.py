"""Application configuration.

All runtime configuration is sourced from environment variables (or a local
``.env`` file) and validated once at import time.  Nothing in the codebase
should read ``os.environ`` directly -- add a field here instead.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def normalise_database_url(url: str) -> str:
    """Map the URL forms hosting providers emit onto the driver we install.

    Managed Postgres -- Neon, Render, Supabase, Heroku -- hands out a
    ``postgres://`` URL. SQLAlchemy 2 removed that alias and rejects it
    outright. The ``postgresql://`` form is accepted but resolves to psycopg2,
    which is not in this project's dependency set (``.[postgres]`` installs
    psycopg 3), so it fails at connect time with a missing-driver error.

    Both are rewritten to the driver actually present. A URL that names its
    driver explicitly is left alone: overriding a deliberate choice would hide
    the real problem rather than fix it.
    """
    if not url:
        return url
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    if scheme.lower() in {"postgres", "postgresql"}:
        return f"postgresql+psycopg://{rest}"
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app ---
    app_name: str = "BioIntel"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False
    api_prefix: str = "/api/v1"

    #: Comma separated list of allowed CORS origins.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ----------------------------------------------------------- database ---
    #: SQLAlchemy URL.  Defaults to a file-backed SQLite database so the
    #: application boots on a laptop with zero infrastructure.  Point this at
    #: PostgreSQL in any shared environment.
    database_url: str = ""
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ------------------------------------------------------------ storage ---
    #: Root directory for uploaded documents and derived page renders.
    storage_dir: Path = REPO_ROOT / "storage"
    max_upload_mb: int = 50
    max_pdf_pages: int = 400

    # ---------------------------------------------------------------- llm ---
    llm_provider: Literal["openai", "stub"] = "openai"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_organization: str | None = None

    #: Deep reasoning: claim extraction, adjudication, report synthesis.
    model_reasoning: str = "gpt-5"
    #: High-throughput: query generation, classification, short summaries.
    model_fast: str = "gpt-5-mini"
    #: Multimodal page understanding (charts, diagrams, scanned pages).
    model_vision: str = "gpt-5"
    #: Entity extraction over small page chunks. This is a recognition task
    #: against a fixed type list, not a reasoning one, and it runs once per
    #: chunk -- the fast model is both quicker and materially cheaper here.
    model_extraction: str = "gpt-5-mini"
    model_embedding: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    llm_timeout_seconds: float = 180.0
    llm_max_retries: int = 4
    #: Absolute ceiling on one response, reasoning tokens included. Runaway
    #: output is bounded by chunk size, not by this -- its job is to stop a
    #: pathological call, so it must sit well clear of a legitimate answer
    #: plus its reasoning. At 16,000 a claim chunk's content budget and a
    #: reasoning model's reserve summed to exactly the cap, leaving no
    #: headroom at all.
    llm_max_output_tokens: int = 32000
    #: Reasoning effort for models that support it ("minimal" | "low" | "medium" | "high").
    llm_reasoning_effort: str = "medium"
    #: Effort for mechanical extraction over a handful of pages. Deliberately
    #: lower than the default: extended reasoning adds latency without adding
    #: recall when the task is "list what is named on these pages".
    llm_extraction_reasoning_effort: str = "low"
    #: Hard ceiling on total LLM calls for a single analysis run (cost guard).
    llm_max_calls_per_run: int = 400
    #: Concurrency limit for parallel LLM calls.
    llm_concurrency: int = 12

    # ------------------------------------------------------- call budgets ---
    #: Ceiling on the input side of any single model call. Prompts above this
    #: are logged as a defect; the chunked extraction stages treat them as a
    #: hard error. A single oversized call is slower and less accurate than
    #: several small ones run in parallel.
    llm_max_input_tokens: int = 20_000
    #: Target input size for one extraction chunk, leaving headroom under
    #: ``llm_max_input_tokens`` for the prompt template and system message.
    extraction_chunk_input_tokens: int = 12_000
    #: Pages per extraction chunk. Small enough that the output of one call
    #: cannot approach the output-token ceiling, large enough that a claim
    #: and its caveat on the following slide stay in the same call.
    extraction_chunk_max_pages: int = 8
    #: Output ceiling for one extraction chunk. Reaching it means the chunk
    #: was too big; the stage splits and retries rather than losing the chunk.
    extraction_chunk_output_tokens: int = 6_000
    #: Output ceiling for reading one page. A page transcription plus its
    #: chart values does not need the global 16k budget, and allowing it
    #: lets a single confused page burn minutes of wall clock.
    vision_max_output_tokens: int = 6_000
    #: Reasoning effort for page reading. Transcribing a slide and naming what
    #: a chart shows is mostly perception; on a 25-page deck the default
    #: effort cost ~57 seconds per page, which no amount of concurrency brings
    #: inside a two-minute budget. Raise this to "medium" if chart-value
    #: recovery on dense figures matters more than turnaround time.
    vision_reasoning_effort: str = "low"

    # --------------------------------------------------------- retrieval ---
    ncbi_api_key: str | None = None
    #: NCBI requires a contact e-mail in the User-Agent for E-utilities.
    ncbi_tool_email: str = "engineering@biointel.example"
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    europepmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    clinicaltrials_base_url: str = "https://clinicaltrials.gov/api/v2"
    openalex_base_url: str = "https://api.openalex.org"
    #: openFDA is the authoritative source for U.S. approval status. No key is
    #: required; a free key raises the rate limit from 240 to 1000 req/min.
    openfda_base_url: str = "https://api.fda.gov"
    openfda_api_key: str | None = None
    #: Attempt authoritative verification of regulatory and pipeline claims.
    regulatory_verification_enabled: bool = True
    #: Max claims sent for authoritative verification in one run.
    max_verification_claims: int = 30

    retrieval_enabled: bool = True
    retrieval_timeout_seconds: float = 30.0
    retrieval_max_retries: int = 3
    retrieval_concurrency: int = 4
    #: Max literature records fetched per generated query.
    retrieval_page_size: int = 25
    #: Max evidence items retained per claim after ranking.
    evidence_per_claim: int = 8
    #: Cache TTL for external literature responses.
    retrieval_cache_ttl_hours: int = 168

    # -------------------------------------------------------------- jobs ---
    #: ``inline`` runs the pipeline in the API process (useful for tests and
    #: single-user local runs); ``worker`` requires ``biointel-worker`` running.
    job_execution_mode: Literal["worker", "inline"] = "worker"
    job_poll_interval_seconds: float = 2.0
    job_max_attempts: int = 3
    job_heartbeat_seconds: float = 30.0
    #: A job whose heartbeat is older than this is considered abandoned.
    job_stale_after_seconds: float = 300.0
    worker_concurrency: int = 2

    # ---------------------------------------------------------- security ---
    #: Comma separated API keys accepted by the API. Empty disables auth
    #: (only permitted outside production).
    api_keys: str = ""
    secret_key: str = "dev-insecure-secret-change-me"
    rate_limit_per_minute: int = 60
    upload_rate_limit_per_hour: int = 30

    # -------------------------------------------------------- validators ---
    @field_validator("storage_dir", mode="before")
    @classmethod
    def _expand_storage(cls, v: object) -> object:
        if isinstance(v, str):
            return Path(os.path.expandvars(v)).expanduser()
        return v

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_database_url(cls, v: object) -> object:
        return normalise_database_url(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _defaults_and_invariants(self) -> Settings:
        if not self.database_url:
            db_path = (self.storage_dir / "biointel.db").as_posix()
            object.__setattr__(self, "database_url", f"sqlite+pysqlite:///{db_path}")
        if self.llm_provider == "openai" and not self.openai_api_key:
            # Fall back to the deterministic offline provider so the product is
            # runnable without credentials; loudly flagged at startup.
            object.__setattr__(self, "llm_provider", "stub")
        if self.environment == "production":
            # These abort startup, so the message is the only diagnostic a
            # platform log will carry. Say what to do, not just what is wrong.
            if not self.api_keys:
                raise ValueError(
                    "API_KEYS must be set when ENVIRONMENT=production; the API would "
                    "otherwise accept unauthenticated requests. Set it to one or more "
                    "comma-separated secrets, e.g. "
                    'python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )
            if self.secret_key == "dev-insecure-secret-change-me":
                raise ValueError(
                    "SECRET_KEY is still the built-in development value; set it to a "
                    "generated secret when ENVIRONMENT=production, e.g. "
                    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )
        return self

    # ------------------------------------------------------------ helpers ---
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key_set)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def renders_dir(self) -> Path:
        return self.storage_dir / "renders"

    @property
    def metrics_dir(self) -> Path:
        return self.storage_dir / "metrics"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_directories(self) -> None:
        for path in (self.storage_dir, self.uploads_dir, self.renders_dir, self.metrics_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
