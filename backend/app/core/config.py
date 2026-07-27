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
    model_embedding: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    llm_timeout_seconds: float = 180.0
    llm_max_retries: int = 4
    llm_max_output_tokens: int = 16000
    #: Reasoning effort for models that support it ("minimal" | "low" | "medium" | "high").
    llm_reasoning_effort: str = "medium"
    #: Hard ceiling on total LLM calls for a single analysis run (cost guard).
    llm_max_calls_per_run: int = 400
    #: Concurrency limit for parallel LLM calls.
    llm_concurrency: int = 6

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
            if not self.api_keys:
                raise ValueError("API_KEYS must be set in production")
            if self.secret_key == "dev-insecure-secret-change-me":
                raise ValueError("SECRET_KEY must be changed in production")
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
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_directories(self) -> None:
        for path in (self.storage_dir, self.uploads_dir, self.renders_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
