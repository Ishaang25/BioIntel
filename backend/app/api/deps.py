"""FastAPI dependencies: auth, rate limiting, pagination."""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import RateLimited, Unauthorized
from app.core.logging import get_logger
from app.core.upload_tickets import redeem_ticket
from app.db.session import get_db

log = get_logger(__name__)

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(slots=True)
class Principal:
    """Who is making the request."""

    key_id: str
    is_anonymous: bool = False
    #: True when `key_id` names the *authentication method* rather than the
    #: caller, so every caller using it collapses onto one value. An upload
    #: ticket is deliberately anonymous by design -- it carries no identity, it
    #: carries permission -- which makes `key_id` useless as a rate-limit key.
    is_shared: bool = False

    @property
    def label(self) -> str:
        return "anonymous" if self.is_anonymous else self.key_id

    def rate_limit_identity(self, request: Request) -> str:
        """What to count requests against.

        An API key identifies its holder, so it is its own bucket. Anonymous
        and ticket-bearing callers do not identify themselves at all; counting
        them under a shared constant would give every such caller in the world
        one bucket between them, where a single busy user exhausts the limit
        for everyone. The client address is the closest available identity.
        """
        if (self.is_anonymous or self.is_shared) and request.client:
            return request.client.host
        return self.key_id


def _key_id(api_key: str) -> str:
    """Stable, non-reversible identifier for logging (never log the key)."""
    return f"key_{api_key[:4]}…{api_key[-2:]}" if len(api_key) > 8 else "key_short"


async def require_principal(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Authenticate the caller.

    Auth is disabled when no API keys are configured, which is only permitted
    outside production (enforced in :class:`app.core.config.Settings`).
    """
    if not settings.auth_enabled:
        return Principal(key_id="local", is_anonymous=True)

    presented = x_api_key
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()

    if not presented:
        raise Unauthorized("Provide an API key via the X-API-Key header.")

    # Constant-time comparison against every configured key.
    for candidate in settings.api_key_set:
        if hmac.compare_digest(presented, candidate):
            return Principal(key_id=_key_id(candidate))

    raise Unauthorized()


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]


async def require_upload_principal(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_upload_ticket: Annotated[str | None, Header(alias="X-Upload-Ticket")] = None,
) -> Principal:
    """Authenticate an upload by API key *or* by a signed upload ticket.

    A browser cannot hold the API key, and on a serverless host it cannot send
    a large file through the key-holding proxy either -- the platform caps the
    body long before our code sees it.  A ticket is the narrow credential that
    lets the browser post the file straight here; see
    :mod:`app.core.upload_tickets`.

    The key path is unchanged, so every existing client -- the proxy, the CLI,
    the tests -- behaves exactly as before.

    The result is memoised on the request.  A ticket is single-use, and this
    dependency is resolved both by the route and by its rate limiter; relying
    on FastAPI's per-request dependency cache to make that one redemption
    rather than two would be a correctness bug waiting on an implementation
    detail.
    """
    cached = getattr(request.state, "upload_principal", None)
    if cached is not None:
        return cached

    if x_upload_ticket:
        redeem_ticket(x_upload_ticket)
        # `is_shared`: every ticket bearer presents this same key_id, so it
        # names the door they came through, not who they are. Uploads are
        # counted per client address instead -- see `rate_limit_identity`.
        principal = Principal(key_id="upload_ticket", is_shared=True)
    else:
        principal = await require_principal(x_api_key=x_api_key, authorization=authorization)

    request.state.upload_principal = principal
    return principal


UploadPrincipal = Annotated[Principal, Depends(require_upload_principal)]


class SlidingWindowLimiter:
    """In-process sliding-window rate limiter.

    Adequate for a single API instance.  Behind multiple replicas this should
    move to Redis; the interface stays the same.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, window_seconds: float) -> None:
        if limit <= 0:
            return
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(bucket[0] + window_seconds - now) + 1)
            raise RateLimited(
                f"Rate limit of {limit} requests per {int(window_seconds)}s exceeded.",
                detail={"retry_after_seconds": retry_after},
            )
        bucket.append(now)

    def reset(self) -> None:
        self._hits.clear()


_limiter = SlidingWindowLimiter()


def rate_limit(request: Request, principal: CurrentPrincipal) -> None:
    _limiter.check(
        f"api:{principal.rate_limit_identity(request)}",
        limit=settings.rate_limit_per_minute,
        window_seconds=60.0,
    )


def upload_rate_limit(request: Request, principal: UploadPrincipal) -> None:
    _limiter.check(
        f"upload:{principal.rate_limit_identity(request)}",
        limit=settings.upload_rate_limit_per_hour,
        window_seconds=3600.0,
    )


def reset_rate_limits() -> None:
    """Test helper."""
    _limiter.reset()


@dataclass(slots=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


PaginationDep = Annotated[Pagination, Depends(pagination)]
