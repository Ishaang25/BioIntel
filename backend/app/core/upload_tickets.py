"""Short-lived signed tickets that let a browser upload straight to the API.

Why this exists
---------------
The browser never talks to this API directly: it goes through the frontend's
server-side proxy, which holds the API key.  Serverless hosts cap how large a
request body a function is allowed to *receive*, and on Vercel that ceiling is
4.5 MB on every plan and is not configurable.  A deck above it is rejected at
the edge with ``413`` before any application code runs -- ours or theirs --
even though the file sits comfortably inside this API's own 50 MB limit.

A ticket takes the file off that path without putting the API key in the
browser.  The frontend's server asks for a ticket (authenticated normally, with
the key), hands the browser the opaque token, and the browser posts the file
directly to this API with the token attached.  The proxy is bypassed, so the
platform's body cap never applies.

Properties
----------
* **Signed.** HMAC-SHA256 over the expiry and nonce with ``SECRET_KEY``.  A
  ticket cannot be forged or extended without the secret.
* **Short-lived.** Minutes, not hours -- long enough to pick a file and push it
  over a slow connection, short enough that a leaked token is worthless.
* **Single-use.** A redeemed nonce is remembered until it expires, so a token
  captured from a log or a browser extension cannot be replayed.
* **Narrow.** A ticket authorises one thing -- creating a document.  It is not
  a session and cannot read, list or delete anything.

Where the replay record lives
-----------------------------
:class:`InProcessLedger`, in the memory of the API process.  That is exact for
the deployment as it stands -- one instance, one uvicorn process, no
``--workers`` -- and it costs nothing on the upload path.

It is *not* correct across replicas: a replay landing on a second instance
would find no record of the nonce and be accepted.  Running BioIntel on more
than one replica is not possible today for older and larger reasons: uploaded
PDFs and page renders are written to the instance's local disk and addressed by
absolute path (``app.services.documents``), so a request routed to the wrong
replica cannot find the document at all, and the rate limiter in
``app.api.deps`` keeps its counters in the same process.  Sharding this ledger
alone would fix the least of those three and imply a guarantee the rest of the
system does not make.

When that changes, the store is the only thing that has to: implement
:class:`ReplayLedger` and install it with :func:`set_replay_ledger` at startup.
Nothing else in this module or its callers needs to know.  The natural
implementation is the primary database rather than a new dependency -- a
``UNIQUE`` nonce column and ``INSERT ... ON CONFLICT DO NOTHING`` is exactly the
"claim exactly once" primitive, in the store the job queue already uses for the
same reason (see ``app.jobs.queue``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings
from app.core.errors import Unauthorized
from app.core.logging import get_logger

log = get_logger(__name__)

#: Token format version. Bumped if the payload layout ever changes, so an
#: old token fails cleanly rather than being misparsed.
_VERSION = "t1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sign(payload: str) -> str:
    digest = hmac.new(settings.secret_key.encode("utf-8"), payload.encode("ascii"), hashlib.sha256)
    return _b64(digest.digest())


@dataclass(frozen=True, slots=True)
class UploadTicket:
    token: str
    expires_at: float
    max_bytes: int

    @property
    def ttl_seconds(self) -> int:
        return max(0, int(self.expires_at - time.time()))


class ReplayLedger(Protocol):
    """Where redeemed nonces are remembered.

    The one seam between "a ticket may be used once" and where that fact is
    stored.  An implementation has to provide exactly one guarantee: for a
    given nonce, :meth:`claim` returns ``True`` to at most one caller.
    """

    def claim(self, nonce: str, expires_at: float) -> bool:
        """Record `nonce` as used. ``False`` if it had already been redeemed."""
        ...

    def reset(self) -> None:
        """Forget every record. Used by tests; a no-op is acceptable."""
        ...


class InProcessLedger:
    """The default ledger: a dictionary in this process.

    Bounded by the ticket TTL rather than by count -- entries are dropped as
    soon as the signature they belong to would be rejected anyway -- so the
    resident set is however many tickets were redeemed in the last few minutes.

    Thread-safe. Not shared between processes; see the module docstring for why
    that is the right trade today and what replaces it when it stops being.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, nonce: str, expires_at: float) -> bool:
        now = time.time()
        with self._lock:
            if self._seen:
                expired = [key for key, deadline in self._seen.items() if deadline <= now]
                for key in expired:
                    del self._seen[key]
            if nonce in self._seen:
                return False
            self._seen[nonce] = expires_at
            return True

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()


_ledger: ReplayLedger = InProcessLedger()


def set_replay_ledger(ledger: ReplayLedger) -> None:
    """Install an alternative replay store.

    Call once during startup, before the first request. This exists so that
    moving off the in-process ledger is a new class plus this one line, rather
    than an edit to the redemption path.
    """
    global _ledger
    _ledger = ledger


def replay_ledger() -> ReplayLedger:
    """The ledger currently in use."""
    return _ledger


def issue_ticket() -> UploadTicket:
    """Mint a ticket authorising one direct document upload."""
    expires_at = time.time() + settings.upload_ticket_ttl_seconds
    nonce = secrets.token_urlsafe(12)
    payload = f"{_VERSION}.{int(expires_at)}.{nonce}"
    return UploadTicket(
        token=f"{payload}.{_sign(payload)}",
        expires_at=expires_at,
        max_bytes=settings.max_upload_bytes,
    )


def redeem_ticket(token: str) -> None:
    """Validate and consume `token`, or raise :class:`Unauthorized`.

    The failure message is deliberately uniform across malformed, forged and
    expired tokens except where the distinction helps a legitimate client: an
    expired ticket is worth naming, because the fix is "ask for another one".
    """
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != _VERSION:
        raise Unauthorized("The upload ticket is not valid.")

    version, expiry_raw, nonce, signature = parts
    payload = f"{version}.{expiry_raw}.{nonce}"
    if not hmac.compare_digest(signature, _sign(payload)):
        raise Unauthorized("The upload ticket is not valid.")

    try:
        expires_at = float(expiry_raw)
    except ValueError:
        raise Unauthorized("The upload ticket is not valid.") from None

    if expires_at <= time.time():
        raise Unauthorized("The upload ticket has expired. Start the upload again.")

    if not _ledger.claim(nonce, expires_at):
        # The only signal that would show this mechanism being probed. A
        # legitimate client redeems each ticket exactly once, so every line
        # here is either an attempted replay or a retry after a response the
        # client never saw.
        log.warning("upload_ticket.replay_rejected", nonce=nonce)
        raise Unauthorized("The upload ticket has already been used.")


def reset_ledger() -> None:
    """Test helper: forget every redeemed nonce."""
    _ledger.reset()
