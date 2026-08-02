"""Direct browser uploads: the path that exists because the proxy has a ceiling.

A Vercel Serverless Function may receive at most 4.5 MB of request body, so a
deck above that never reaches the frontend proxy that holds the API key. These
tests cover the alternative: a ticket minted server-to-server, redeemed once by
the browser posting straight here.
"""

from __future__ import annotations

import io

import pytest

from app.core.config import get_settings
from app.core.upload_tickets import reset_ledger
from tests.fixtures.sample_deck import neurogen_pdf

API = "/api/v1"


def post_deck(client, headers: dict[str, str] | None = None, data: bytes | None = None):
    return client.post(
        f"{API}/documents",
        files={
            "file": (
                "deck.pdf",
                io.BytesIO(data if data is not None else neurogen_pdf()),
                "application/pdf",
            )
        },
        data={"analyze": "false"},
        headers=headers or {},
    )


@pytest.fixture(autouse=True)
def _clean_ledger():
    reset_ledger()
    yield
    reset_ledger()


@pytest.fixture
def secured_client(monkeypatch):
    """A client with auth enabled, which is the configuration that matters here."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr(get_settings(), "api_keys", "secret-key-1")
    with TestClient(create_app()) as test_client:
        yield test_client


class TestTicketIssuance:
    def test_ticket_requires_the_api_key(self, secured_client):
        assert secured_client.post(f"{API}/documents/upload-ticket").status_code == 401

    def test_ticket_is_issued_to_a_key_holder(self, secured_client):
        response = secured_client.post(
            f"{API}/documents/upload-ticket", headers={"X-API-Key": "secret-key-1"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token"]
        assert body["expires_in_seconds"] > 0
        assert body["max_bytes"] == get_settings().max_upload_bytes

    def test_a_ticket_cannot_be_used_to_mint_another(self, secured_client):
        token = secured_client.post(
            f"{API}/documents/upload-ticket", headers={"X-API-Key": "secret-key-1"}
        ).json()["token"]
        # Tickets authorise uploading, and nothing else.
        assert (
            secured_client.post(
                f"{API}/documents/upload-ticket", headers={"X-Upload-Ticket": token}
            ).status_code
            == 401
        )


class TestTicketRedemption:
    def test_upload_with_a_ticket_succeeds(self, secured_client):
        token = secured_client.post(
            f"{API}/documents/upload-ticket", headers={"X-API-Key": "secret-key-1"}
        ).json()["token"]

        response = post_deck(secured_client, headers={"X-Upload-Ticket": token})
        assert response.status_code == 201, response.text
        assert response.json()["document"]["filename"] == "deck.pdf"

    def test_upload_without_any_credential_is_rejected(self, secured_client):
        assert post_deck(secured_client).status_code == 401

    def test_a_forged_ticket_is_rejected(self, secured_client):
        assert (
            post_deck(secured_client, headers={"X-Upload-Ticket": "t1.9999999999.x.y"}).status_code
            == 401
        )

    def test_a_ticket_cannot_be_replayed(self, secured_client):
        token = secured_client.post(
            f"{API}/documents/upload-ticket", headers={"X-API-Key": "secret-key-1"}
        ).json()["token"]

        assert post_deck(secured_client, headers={"X-Upload-Ticket": token}).status_code == 201
        # The route and its rate limiter both resolve the upload principal; a
        # second *request* must fail, but that first upload must not have burned
        # the ticket twice and failed on itself.
        assert post_deck(secured_client, headers={"X-Upload-Ticket": token}).status_code == 401

    def test_the_api_key_path_still_works(self, secured_client):
        response = post_deck(secured_client, headers={"X-API-Key": "secret-key-1"})
        assert response.status_code == 201


class TestSizeCeiling:
    def test_an_oversized_body_is_refused_on_its_declared_length(self, client, monkeypatch):
        # One megabyte ceiling, a two-megabyte body: the request is answered
        # from Content-Length without the payload ever being read.
        monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
        response = post_deck(client, data=b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024))
        assert response.status_code == 413
        body = response.json()
        assert body["code"] == "document_too_large"
        assert body["detail"]["limit_bytes"] == 1024 * 1024
        assert "1 MB" in body["message"]

    def test_a_file_inside_the_ceiling_is_accepted(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
        assert post_deck(client).status_code == 201
