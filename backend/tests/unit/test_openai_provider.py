"""OpenAI provider.

This is the production path but never runs in CI without credentials, so it is
tested against a fake SDK client that mimics the Responses API's shapes. What
matters here is request construction (reasoning models reject `temperature`,
non-reasoning models reject `reasoning`), usage accounting, and the failure
behaviour: retry on transient errors, fall back to non-strict schemas when the
API rejects one, and surface permanent errors immediately.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, BadRequestError, InternalServerError, RateLimitError

from app.core.errors import LLMError
from app.llm.base import ImagePart, LLMRequest
from app.llm.openai_provider import OpenAIProvider, is_reasoning_model
from app.llm.schemas import ClaimVerdictOut

VALID_PAYLOAD = {
    "verdict": "The literature is consistent with the claim.",
    "key_uncertainties": ["Species difference"],
    "novelty": 0.4,
    "translational_gap": None,
}


def make_response(
    text: str | None = None,
    *,
    status: str = "completed",
    input_tokens: int = 120,
    output_tokens: int = 45,
    reasoning_tokens: int = 30,
    cached_tokens: int = 20,
) -> SimpleNamespace:
    return SimpleNamespace(
        output_text=text if text is not None else json.dumps(VALID_PAYLOAD),
        model="gpt-5-2025-08-07",
        status=status,
        incomplete_details=SimpleNamespace(reason="max_output_tokens")
        if status == "incomplete"
        else None,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
            input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        ),
    )


class FakeResponses:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeEmbeddings:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in self.vectors])


class FakeClient:
    def __init__(self, outcomes: list[object], vectors: list[list[float]] | None = None) -> None:
        self.responses = FakeResponses(outcomes)
        self.embeddings = FakeEmbeddings(vectors or [])
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def make_request(model: str = "gpt-5", **overrides) -> LLMRequest:
    defaults = {
        "purpose": "claim_verdict",
        "system": "system prompt",
        "user": "user prompt",
        "schema_name": "ClaimVerdictOut",
        "schema_model": ClaimVerdictOut,
        "model": model,
    }
    return LLMRequest(**{**defaults, **overrides})


def bad_request(message: str) -> BadRequestError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(400, request=request, json={"error": {"message": message}})
    return BadRequestError(message, response=response, body=None)


def rate_limited() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(429, request=request, json={"error": {"message": "slow down"}})
    return RateLimitError("rate limited", response=response, body=None)


def server_error() -> InternalServerError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(500, request=request, json={"error": {"message": "boom"}})
    return InternalServerError("server error", response=response, body=None)


class TestModelDetection:
    @pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "o1", "o3-mini", "o4-mini"])
    def test_reasoning_models(self, model):
        assert is_reasoning_model(model) is True

    @pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4o-mini", "text-embedding-3-small"])
    def test_non_reasoning_models(self, model):
        assert is_reasoning_model(model) is False


class TestRequestConstruction:
    async def test_reasoning_model_sends_effort_and_no_temperature(self):
        client = FakeClient([make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        await provider.complete_structured(make_request("gpt-5", temperature=0.4))

        kwargs = client.responses.calls[0]
        assert kwargs["reasoning"] == {"effort": "medium"}
        assert "temperature" not in kwargs

    async def test_non_reasoning_model_sends_temperature_and_no_reasoning(self):
        client = FakeClient([make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        await provider.complete_structured(make_request("gpt-4.1", temperature=0.2))

        kwargs = client.responses.calls[0]
        assert kwargs["temperature"] == 0.2
        assert "reasoning" not in kwargs

    async def test_strict_json_schema_is_attached(self):
        client = FakeClient([make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        await provider.complete_structured(make_request())

        text_format = client.responses.calls[0]["text"]["format"]
        assert text_format["type"] == "json_schema"
        assert text_format["strict"] is True
        assert text_format["name"] == "ClaimVerdictOut"
        assert text_format["schema"]["additionalProperties"] is False

    async def test_system_prompt_goes_to_instructions(self):
        client = FakeClient([make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        await provider.complete_structured(make_request())
        assert client.responses.calls[0]["instructions"] == "system prompt"

    async def test_images_are_attached_as_data_urls(self):
        client = FakeClient([make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        await provider.complete_structured(
            make_request(images=[ImagePart(data=b"\x89PNG\r\n\x1a\nfake")])
        )

        content = client.responses.calls[0]["input"][0]["content"]
        assert content[0]["type"] == "input_text"
        assert content[1]["type"] == "input_image"
        assert content[1]["image_url"].startswith("data:image/png;base64,")

    async def test_max_output_tokens_is_honoured(self):
        client = FakeClient([make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        await provider.complete_structured(make_request(max_output_tokens=2048))
        assert client.responses.calls[0]["max_output_tokens"] == 2048


class TestResponseHandling:
    async def test_usage_is_extracted(self):
        client = FakeClient([make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        result = await provider.complete_structured(make_request())

        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 45
        assert result.usage.reasoning_tokens == 30
        assert result.usage.cached_input_tokens == 20
        assert result.model == "gpt-5-2025-08-07"
        assert result.provider == "openai"

    async def test_incomplete_response_is_flagged_not_discarded(self):
        client = FakeClient([make_response(status="incomplete")])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        result = await provider.complete_structured(make_request())
        assert result.truncated is True
        assert result.text  # partial content is still returned

    async def test_empty_response_raises(self):
        client = FakeClient([make_response(text="   ")])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        with pytest.raises(LLMError, match="empty"):
            await provider.complete_structured(make_request())

    async def test_missing_usage_does_not_crash(self):
        response = make_response()
        response.usage = None
        client = FakeClient([response])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        result = await provider.complete_structured(make_request())
        assert result.usage.total_tokens == 0


class TestFailureHandling:
    async def test_rate_limit_is_retried(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "llm_max_retries", 3)
        client = FakeClient([rate_limited(), rate_limited(), make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]

        result = await provider.complete_structured(make_request())
        assert json.loads(result.text)["novelty"] == 0.4
        assert result.attempts == 3

    async def test_server_error_is_retried(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "llm_max_retries", 2)
        client = FakeClient([server_error(), make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        assert await provider.complete_structured(make_request())

    async def test_connection_error_is_retried(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "llm_max_retries", 2)
        error = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
        client = FakeClient([error, make_response()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        assert await provider.complete_structured(make_request())

    async def test_retries_are_bounded(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "llm_max_retries", 2)
        client = FakeClient([rate_limited()])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]

        with pytest.raises(RateLimitError):
            await provider.complete_structured(make_request())
        assert len(client.responses.calls) == 2

    async def test_schema_rejection_falls_back_to_non_strict(self):
        """A schema the API refuses must not fail the whole stage."""
        client = FakeClient(
            [bad_request("Invalid json_schema: unsupported keyword"), make_response()]
        )
        provider = OpenAIProvider(client)  # type: ignore[arg-type]

        result = await provider.complete_structured(make_request())
        assert json.loads(result.text)["novelty"] == 0.4
        assert client.responses.calls[0]["text"]["format"]["strict"] is True
        assert client.responses.calls[1]["text"]["format"]["strict"] is False

    async def test_other_bad_requests_fail_immediately(self):
        client = FakeClient([bad_request("model 'gpt-9' does not exist")])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]

        with pytest.raises(LLMError, match="rejected"):
            await provider.complete_structured(make_request())
        assert len(client.responses.calls) == 1


class TestEmbeddings:
    async def test_dimensions_are_sent_for_v3_models(self, settings):
        client = FakeClient([], vectors=[[0.1, 0.2]])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]

        result = await provider.embed(["text"], model="text-embedding-3-small", dimensions=256)
        assert result == [[0.1, 0.2]]
        assert client.embeddings.calls[0]["dimensions"] == 256

    async def test_dimensions_are_omitted_for_other_models(self):
        client = FakeClient([], vectors=[[0.1]])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        await provider.embed(["t"], model="some-other-embedding", dimensions=256)
        assert "dimensions" not in client.embeddings.calls[0]

    async def test_empty_input_makes_no_call(self):
        client = FakeClient([])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        assert await provider.embed([], model="text-embedding-3-small", dimensions=256) == []
        assert client.embeddings.calls == []


class TestLifecycle:
    async def test_close_releases_the_client(self):
        client = FakeClient([])
        provider = OpenAIProvider(client)  # type: ignore[arg-type]
        await provider.aclose()
        assert client.closed is True

    def test_missing_api_key_is_reported_clearly(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", None)
        with pytest.raises(LLMError, match="OPENAI_API_KEY"):
            OpenAIProvider()
