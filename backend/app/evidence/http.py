"""Shared HTTP plumbing for external literature sources.

Provides three things every source needs and none should re-implement:

* **Politeness.** A per-host token bucket that respects each provider's stated
  rate limit.  NCBI in particular will block a client that exceeds 3 req/s
  without an API key.
* **Resilience.** Bounded exponential backoff on transient failures, with
  permanent failures surfaced immediately rather than retried.
* **Caching.** A database-backed response cache keyed by the exact request.
  Literature does not change minute to minute, and re-running an analysis
  should not re-hammer PubMed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.errors import RetrievalError
from app.core.logging import get_logger
from app.db.models import RetrievalCache
from app.db.session import session_scope

log = get_logger(__name__)

#: Identifies the application *and* the underlying HTTP client.  The latter is
#: not decoration: ClinicalTrials.gov's edge returns 403 to agents it does not
#: recognise as a known client library.  NCBI's separate identification
#: requirement is met by the ``tool``/``email`` query parameters that
#: :mod:`app.evidence.pubmed` sends on every request.
USER_AGENT = f"BioIntel/{__import__('app').__version__} python-httpx/{httpx.__version__}"

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class RateLimit:
    """Serialises requests to a host at a fixed maximum rate."""

    __slots__ = ("_last", "_lock", "rate_per_second")

    def __init__(self, rate_per_second: float) -> None:
        self.rate_per_second = rate_per_second
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        interval = 1.0 / self.rate_per_second if self.rate_per_second > 0 else 0.0
        async with self._lock:
            now = time.monotonic()
            wait = self._last + interval - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._last = now


def cache_key(url: str, params: dict[str, Any] | None) -> str:
    canonical = url + "?" + urlencode(sorted((params or {}).items()))
    return hashlib.sha256(canonical.encode()).hexdigest()


def cache_get(key: str) -> dict[str, Any] | None:
    now = dt.datetime.now(dt.UTC)
    try:
        with session_scope() as session:
            row = session.get(RetrievalCache, key)
            if row is None:
                return None
            if row.expires_at <= now:
                session.delete(row)
                return None
            return dict(row.payload)
    except Exception:  # cache must never be load-bearing
        log.warning("retrieval.cache_read_failed", exc_info=True)
        return None


def cache_put(key: str, *, source: str, url: str, payload: dict[str, Any]) -> None:
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(hours=settings.retrieval_cache_ttl_hours)
    try:
        with session_scope() as session:
            existing = session.get(RetrievalCache, key)
            if existing is not None:
                existing.payload = payload
                existing.fetched_at = dt.datetime.now(dt.UTC)
                existing.expires_at = expires
            else:
                session.add(
                    RetrievalCache(
                        cache_key=key,
                        source=source,
                        request_url=url[:2000],
                        payload=payload,
                        expires_at=expires,
                    )
                )
    except Exception:
        log.warning("retrieval.cache_write_failed", exc_info=True)


def purge_expired_cache() -> int:
    with session_scope() as session:
        result = session.execute(
            delete(RetrievalCache).where(RetrievalCache.expires_at <= dt.datetime.now(dt.UTC))
        )
        return int(result.rowcount or 0)


def cache_stats() -> dict[str, int]:
    with session_scope() as session:
        rows = session.execute(select(RetrievalCache.source)).scalars().all()
    counts: dict[str, int] = {}
    for source in rows:
        counts[source] = counts.get(source, 0) + 1
    return counts


class SourceClient:
    """Base class for a rate-limited, cached, retrying HTTP source."""

    #: Identifier used in cache rows and logs.
    source_name: str = "unknown"
    #: Requests per second this provider tolerates.
    rate_per_second: float = 3.0
    #: Per-source override; some edges reject descriptive agent strings.
    user_agent: str = USER_AGENT

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._limiter = RateLimit(self.rate_per_second)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.retrieval_timeout_seconds),
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                follow_redirects=True,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        parse: str = "json",
        use_cache: bool = True,
    ) -> Any:
        """GET with caching, rate limiting and retries.

        ``parse`` is ``"json"`` or ``"text"``.  Text responses are cached in a
        wrapper object so the cache column stays JSON-typed.
        """
        key = cache_key(url, params)
        if use_cache:
            cached = cache_get(key)
            if cached is not None:
                log.debug("retrieval.cache_hit", source=self.source_name, url=url)
                return cached.get("data") if parse == "text" else cached

        client = await self._http()
        last_exc: Exception | None = None

        for attempt in range(1, settings.retrieval_max_retries + 1):
            await self._limiter.acquire()
            try:
                response = await client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                await self._backoff(attempt, reason=str(exc))
                continue

            if response.status_code in _RETRYABLE_STATUS:
                last_exc = RetrievalError(
                    f"{self.source_name} returned {response.status_code}",
                    detail={"status": response.status_code},
                )
                retry_after = _retry_after_seconds(response)
                await self._backoff(
                    attempt, reason=f"HTTP {response.status_code}", floor=retry_after
                )
                continue

            if response.status_code >= 400:
                raise RetrievalError(
                    f"{self.source_name} rejected the request ({response.status_code}).",
                    detail={"status": response.status_code, "url": url},
                )

            payload = self._decode(response, parse)
            if use_cache:
                to_cache = {"data": payload} if parse == "text" else payload
                if isinstance(to_cache, dict):
                    cache_put(key, source=self.source_name, url=str(response.url), payload=to_cache)
            return payload

        raise RetrievalError(
            f"{self.source_name} was unreachable after {settings.retrieval_max_retries} attempts.",
            detail={"url": url},
            cause=last_exc,
        )

    def _decode(self, response: httpx.Response, parse: str) -> Any:
        if parse == "text":
            return response.text
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise RetrievalError(
                f"{self.source_name} returned a malformed JSON response.", cause=exc
            ) from exc

    async def _backoff(self, attempt: int, *, reason: str, floor: float = 0.0) -> None:
        delay = max(floor, min(20.0, 0.75 * (2 ** (attempt - 1))))
        log.warning(
            "retrieval.retry",
            source=self.source_name,
            attempt=attempt,
            delay_seconds=round(delay, 2),
            reason=reason[:200],
        )
        await asyncio.sleep(delay)

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("retry-after")
    if not value:
        return 0.0
    try:
        return min(30.0, float(value))
    except ValueError:
        return 0.0
