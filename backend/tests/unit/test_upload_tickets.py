"""Direct-upload tickets: signing, expiry and single use.

These exist because a browser cannot hold the API key and, on a serverless
host, cannot route a large file through the proxy that does. See
``app.core.upload_tickets`` for the full reasoning.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.api.deps import Principal
from app.core.errors import Unauthorized
from app.core.upload_tickets import (
    InProcessLedger,
    issue_ticket,
    redeem_ticket,
    replay_ledger,
    reset_ledger,
    set_replay_ledger,
)


@pytest.fixture(autouse=True)
def _clean_ledger():
    reset_ledger()
    yield
    reset_ledger()


class TestIssue:
    def test_ticket_reports_the_api_limit(self):
        from app.core.config import settings

        assert issue_ticket().max_bytes == settings.max_upload_bytes

    def test_ttl_is_positive_and_bounded_by_the_setting(self):
        from app.core.config import settings

        ticket = issue_ticket()
        assert 0 < ticket.ttl_seconds <= settings.upload_ticket_ttl_seconds

    def test_each_ticket_is_unique(self):
        assert issue_ticket().token != issue_ticket().token


class TestRedeem:
    def test_a_fresh_ticket_is_accepted(self):
        redeem_ticket(issue_ticket().token)

    def test_a_ticket_is_accepted_only_once(self):
        token = issue_ticket().token
        redeem_ticket(token)
        with pytest.raises(Unauthorized, match="already been used"):
            redeem_ticket(token)

    @pytest.mark.parametrize(
        "token",
        ["", "nonsense", "t1.1.2", "t1.1.2.3.4", "t9.9999999999.abc.sig"],
    )
    def test_malformed_tokens_are_rejected(self, token: str):
        with pytest.raises(Unauthorized):
            redeem_ticket(token)

    def test_a_tampered_signature_is_rejected(self):
        version, expiry, nonce, signature = issue_ticket().token.split(".")
        forged = f"{version}.{expiry}.{nonce}.{signature[:-1]}x"
        with pytest.raises(Unauthorized):
            redeem_ticket(forged)

    def test_extending_the_expiry_invalidates_the_signature(self):
        version, expiry, nonce, signature = issue_ticket().token.split(".")
        extended = f"{version}.{int(expiry) + 86_400}.{nonce}.{signature}"
        with pytest.raises(Unauthorized):
            redeem_ticket(extended)

    def test_an_expired_ticket_is_rejected_and_says_so(self):
        token = issue_ticket().token
        with (
            patch("app.core.upload_tickets.time.time", return_value=time.time() + 86_400),
            pytest.raises(Unauthorized, match="expired"),
        ):
            redeem_ticket(token)

    def test_a_ticket_signed_with_another_secret_is_rejected(self):
        token = issue_ticket().token
        with (
            patch("app.core.upload_tickets.settings.secret_key", "a-different-secret"),
            pytest.raises(Unauthorized),
        ):
            redeem_ticket(token)


class TestRateLimitIdentity:
    """A ticket says what the bearer may do, never who they are.

    Counting uploads against the constant `key_id` every ticket carries would
    put every direct uploader in the world into one bucket, where one busy
    analyst exhausts the hourly limit for all of them.
    """

    @staticmethod
    def _request(host: str | None):
        return SimpleNamespace(client=SimpleNamespace(host=host) if host else None)

    def test_an_api_key_is_its_own_bucket(self):
        principal = Principal(key_id="key_abcd…yz")
        assert principal.rate_limit_identity(self._request("203.0.113.7")) == "key_abcd…yz"

    def test_ticket_bearers_are_counted_per_client(self):
        principal = Principal(key_id="upload_ticket", is_shared=True)
        assert principal.rate_limit_identity(self._request("203.0.113.7")) == "203.0.113.7"

    def test_two_clients_on_tickets_do_not_share_a_bucket(self):
        principal = Principal(key_id="upload_ticket", is_shared=True)
        first = principal.rate_limit_identity(self._request("203.0.113.7"))
        second = principal.rate_limit_identity(self._request("198.51.100.2"))
        assert first != second

    def test_anonymous_callers_are_still_counted_per_client(self):
        principal = Principal(key_id="local", is_anonymous=True)
        assert principal.rate_limit_identity(self._request("203.0.113.7")) == "203.0.113.7"

    def test_a_request_with_no_client_falls_back_to_the_key_id(self):
        """Better one shared bucket than an unbounded one."""
        principal = Principal(key_id="upload_ticket", is_shared=True)
        assert principal.rate_limit_identity(self._request(None)) == "upload_ticket"


class TestInProcessLedger:
    def test_a_nonce_is_claimable_once(self):
        ledger = InProcessLedger()
        deadline = time.time() + 60
        assert ledger.claim("n1", deadline) is True
        assert ledger.claim("n1", deadline) is False

    def test_distinct_nonces_do_not_collide(self):
        ledger = InProcessLedger()
        deadline = time.time() + 60
        assert ledger.claim("n1", deadline) is True
        assert ledger.claim("n2", deadline) is True

    def test_expired_records_are_swept_rather_than_accumulating(self):
        """The record only has to outlive the signature it belongs to."""
        ledger = InProcessLedger()
        ledger.claim("stale", time.time() - 1)
        ledger.claim("fresh", time.time() + 60)
        assert len(ledger._seen) == 1
        assert "fresh" in ledger._seen

    def test_claims_are_serialised_across_threads(self):
        """Exactly one of N concurrent redemptions of one nonce may win."""
        ledger = InProcessLedger()
        deadline = time.time() + 60
        results: list[bool] = []
        lock = threading.Lock()

        def attempt() -> None:
            won = ledger.claim("contended", deadline)
            with lock:
                results.append(won)

        threads = [threading.Thread(target=attempt) for _ in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results.count(True) == 1, results


class TestLedgerSeam:
    """Moving off the in-process store must be one class and one line.

    These lock in the seam itself, so a future Postgres or Redis ledger is a
    drop-in and the redemption path never has to change.
    """

    def test_the_default_ledger_is_the_in_process_one(self):
        assert isinstance(replay_ledger(), InProcessLedger)

    def test_redemption_goes_through_whatever_ledger_is_installed(self):
        class RecordingLedger:
            def __init__(self) -> None:
                self.claims: list[str] = []

            def claim(self, nonce: str, expires_at: float) -> bool:
                self.claims.append(nonce)
                return True

            def reset(self) -> None:
                self.claims.clear()

        installed = RecordingLedger()
        original = replay_ledger()
        set_replay_ledger(installed)
        try:
            redeem_ticket(issue_ticket().token)
            assert len(installed.claims) == 1
        finally:
            set_replay_ledger(original)

    def test_a_ledger_that_refuses_a_claim_rejects_the_ticket(self):
        """A shared store rejecting a nonce another replica took must 401."""

        class AlwaysSeenLedger:
            def claim(self, nonce: str, expires_at: float) -> bool:
                return False

            def reset(self) -> None:
                return None

        original = replay_ledger()
        set_replay_ledger(AlwaysSeenLedger())
        try:
            with pytest.raises(Unauthorized, match="already been used"):
                redeem_ticket(issue_ticket().token)
        finally:
            set_replay_ledger(original)
