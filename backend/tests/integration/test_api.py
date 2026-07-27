"""API contract tests against the real application."""

from __future__ import annotations

import io

import pytest

from app.core.config import get_settings
from tests.fixtures.sample_deck import neurogen_pdf

API = "/api/v1"


def upload(client, *, data: bytes | None = None, filename: str = "deck.pdf", analyze: bool = False):
    return client.post(
        f"{API}/documents",
        files={
            "file": (
                filename,
                io.BytesIO(data if data is not None else neurogen_pdf()),
                "application/pdf",
            )
        },
        data={"analyze": str(analyze).lower()},
    )


class TestSystem:
    def test_health(self, client):
        body = client.get(f"{API}/health").json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["llm_provider"] == "stub"
        assert body["llm_degraded"] is True

    def test_ready(self, client):
        assert client.get(f"{API}/ready").status_code == 204

    def test_root_points_at_docs(self, client):
        assert client.get("/").json()["docs"] == "/docs"

    def test_openapi_is_valid(self, client):
        spec = client.get("/openapi.json").json()
        assert spec["info"]["title"] == "BioIntel API"
        assert f"{API}/documents" in spec["paths"]

    def test_vocabularies_expose_enums(self, client):
        body = client.get(f"{API}/vocabularies").json()
        assert "mechanism" in body["claim_categories"]
        assert "target" in body["entity_types"]
        assert body["pipeline_stages"][0] == "parse"

    def test_request_id_header_is_returned(self, client):
        response = client.get(f"{API}/health")
        assert response.headers["X-Request-ID"]

    def test_security_headers_are_set(self, client):
        headers = client.get(f"{API}/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"


class TestUpload:
    def test_upload_returns_document(self, client):
        response = upload(client)
        assert response.status_code == 201
        body = response.json()
        assert body["created"] is True
        assert body["document"]["page_count"] == 10
        assert body["document"]["filename"] == "deck.pdf"
        assert body["run"] is None

    def test_upload_with_analyze_creates_a_run(self, client):
        body = upload(client, analyze=True).json()
        assert body["run"] is not None
        assert body["run"]["document_id"] == body["document"]["id"]

    def test_identical_content_is_deduplicated(self, client):
        data = neurogen_pdf()
        first = upload(client, data=data).json()
        second = upload(client, data=data).json()
        assert second["created"] is False
        assert second["document"]["id"] == first["document"]["id"]

    def test_rejects_non_pdf(self, client):
        response = upload(client, data=b"just some text, definitely not a pdf")
        assert response.status_code == 415
        assert response.json()["code"] == "unsupported_document"

    def test_rejects_empty_file(self, client):
        response = upload(client, data=b"")
        assert response.status_code == 415

    def test_rejects_oversized_upload(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
        response = upload(client)
        assert response.status_code == 413
        assert response.json()["code"] == "document_too_large"

    def test_filename_is_sanitised(self, client):
        response = upload(client, filename="../../../etc/passwd.pdf")
        assert "/" not in response.json()["document"]["filename"]
        assert ".." not in response.json()["document"]["filename"]

    def test_missing_file_is_a_validation_error(self, client):
        assert client.post(f"{API}/documents", data={"analyze": "false"}).status_code == 422


class TestDocuments:
    def test_list_is_paginated(self, client):
        upload(client)
        body = client.get(f"{API}/documents", params={"limit": 5}).json()
        assert body["limit"] == 5
        assert body["total"] >= 1
        assert len(body["items"]) <= 5

    def test_pagination_bounds_are_enforced(self, client):
        assert client.get(f"{API}/documents", params={"limit": 5000}).status_code == 422
        assert client.get(f"{API}/documents", params={"offset": -1}).status_code == 422

    def test_get_one(self, client):
        document_id = upload(client).json()["document"]["id"]
        body = client.get(f"{API}/documents/{document_id}").json()
        assert body["id"] == document_id
        assert body["run_count"] == 0

    def test_unknown_document_is_404(self, client):
        response = client.get(f"{API}/documents/doc_does_not_exist")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_download_returns_the_pdf(self, client):
        document_id = upload(client).json()["document"]["id"]
        response = client.get(f"{API}/documents/{document_id}/file")
        assert response.headers["content-type"] == "application/pdf"
        assert response.content[:5] == b"%PDF-"

    def test_pages_are_empty_before_parsing(self, client):
        document_id = upload(client).json()["document"]["id"]
        assert client.get(f"{API}/documents/{document_id}/pages").json() == []

    def test_render_missing_is_404(self, client):
        document_id = upload(client).json()["document"]["id"]
        assert client.get(f"{API}/documents/{document_id}/pages/1/render").status_code == 404

    def test_delete_removes_the_document(self, client):
        document_id = upload(client).json()["document"]["id"]
        assert client.delete(f"{API}/documents/{document_id}").status_code == 204
        assert client.get(f"{API}/documents/{document_id}").status_code == 404


class TestRuns:
    def test_create_run(self, client):
        document_id = upload(client).json()["document"]["id"]
        response = client.post(f"{API}/runs", json={"document_id": document_id})
        assert response.status_code == 202
        assert response.json()["document_id"] == document_id

    def test_run_for_unknown_document_is_404(self, client):
        response = client.post(f"{API}/runs", json={"document_id": "doc_nope"})
        assert response.status_code == 404

    def test_run_detail_includes_stage_plan(self, client):
        document_id = upload(client).json()["document"]["id"]
        run_id = client.post(f"{API}/runs", json={"document_id": document_id}).json()["id"]
        body = client.get(f"{API}/runs/{run_id}").json()
        assert len(body["stages"]) == 10
        assert body["stages"][0]["stage"] == "parse"
        assert body["document"]["id"] == document_id

    def test_list_runs_filters_by_document(self, client):
        first = upload(client).json()["document"]["id"]
        second = upload(client, data=neurogen_pdf()[:-1] + b" ").json()["document"]["id"]
        client.post(f"{API}/runs", json={"document_id": first})
        client.post(f"{API}/runs", json={"document_id": second})
        body = client.get(f"{API}/runs", params={"document_id": first}).json()
        assert body["total"] == 1

    def test_unknown_run_is_404(self, client):
        assert client.get(f"{API}/runs/run_nope").status_code == 404

    def test_report_before_completion_is_404(self, client):
        document_id = upload(client).json()["document"]["id"]
        run_id = client.post(f"{API}/runs", json={"document_id": document_id}).json()["id"]
        assert client.get(f"{API}/runs/{run_id}/report").status_code == 404


class TestAuth:
    @pytest.fixture
    def secured_client(self, monkeypatch):
        from fastapi.testclient import TestClient

        from app.main import create_app

        monkeypatch.setattr(get_settings(), "api_keys", "secret-key-1,secret-key-2")
        with TestClient(create_app()) as test_client:
            yield test_client

    def test_missing_key_is_rejected(self, secured_client):
        response = secured_client.get(f"{API}/documents")
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    def test_wrong_key_is_rejected(self, secured_client):
        response = secured_client.get(f"{API}/documents", headers={"X-API-Key": "nope"})
        assert response.status_code == 401

    def test_valid_key_is_accepted(self, secured_client):
        response = secured_client.get(f"{API}/documents", headers={"X-API-Key": "secret-key-2"})
        assert response.status_code == 200

    def test_bearer_token_is_accepted(self, secured_client):
        response = secured_client.get(
            f"{API}/documents", headers={"Authorization": "Bearer secret-key-1"}
        )
        assert response.status_code == 200

    def test_health_still_requires_no_key(self, secured_client):
        assert secured_client.get(f"{API}/health").status_code == 200


class TestRateLimiting:
    def test_limit_is_enforced_and_advertises_retry_after(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 3)
        statuses = [client.get(f"{API}/documents").status_code for _ in range(5)]
        assert 429 in statuses
        response = client.get(f"{API}/documents")
        assert response.status_code == 429
        assert response.headers["Retry-After"]
        assert response.json()["code"] == "rate_limited"
