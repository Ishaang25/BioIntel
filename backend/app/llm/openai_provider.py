"""OpenAI provider built on the Responses API with strict structured outputs.

Why the Responses API: it is the current first-class surface for reasoning
models, supports server-side JSON-schema constrained decoding (``strict``),
mixed text+image input in one call, and returns reasoning-token accounting we
need for cost telemetry.
"""

from __future__ import annotations

import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import settings
from app.core.errors import LLMError, LLMTruncatedError
from app.core.logging import get_logger
from app.llm.base import LLMRequest, LLMResponse, Usage
from app.llm.json_schema import schema_payload

log = get_logger(__name__)

#: Model families that accept the ``reasoning`` parameter and reject ``temperature``.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

_RETRYABLE = (
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
)


def is_reasoning_model(model: str) -> bool:
    return model.startswith(_REASONING_PREFIXES)


class OpenAIProvider:
    name = "openai"

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            if not settings.openai_api_key:
                raise LLMError("OPENAI_API_KEY is not configured.")
            self._client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                organization=settings.openai_organization,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,  # retries are handled here so they are observable
            )

    # ------------------------------------------------------------ helpers ---
    def _build_input(self, request: LLMRequest) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": request.user}]
        for image in request.images:
            content.append(
                {
                    "type": "input_image",
                    "image_url": image.to_data_url(),
                    "detail": image.detail,
                }
            )
        return [{"role": "user", "content": content}]

    def _build_kwargs(self, request: LLMRequest, *, strict: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "instructions": request.system,
            "input": self._build_input(request),
            "max_output_tokens": request.max_output_tokens or settings.llm_max_output_tokens,
            "text": {
                "format": schema_payload(request.schema_name, request.schema_model, strict=strict)
            },
        }
        if is_reasoning_model(request.model):
            kwargs["reasoning"] = {
                "effort": request.reasoning_effort or settings.llm_reasoning_effort
            }
        elif request.temperature is not None:
            kwargs["temperature"] = request.temperature
        return kwargs

    @staticmethod
    def _usage_from(response: Any) -> Usage:
        usage = getattr(response, "usage", None)
        if usage is None:
            return Usage()
        out_details = getattr(usage, "output_tokens_details", None)
        in_details = getattr(usage, "input_tokens_details", None)
        return Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            reasoning_tokens=int(getattr(out_details, "reasoning_tokens", 0) or 0),
            cached_input_tokens=int(getattr(in_details, "cached_tokens", 0) or 0),
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        text = getattr(response, "output_text", None)
        if text:
            return text
        # Defensive fallback for SDK shapes without the convenience accessor.
        chunks: list[str] = []
        for item in getattr(response, "output", []) or []:
            for part in getattr(item, "content", []) or []:
                value = getattr(part, "text", None)
                if value:
                    chunks.append(value)
        return "".join(chunks)

    # -------------------------------------------------------------- calls ---
    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        attempts = 0
        strict = True
        last_error: Exception | None = None

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.llm_max_retries),
            wait=wait_exponential_jitter(initial=1.0, max=30.0),
            retry=retry_if_exception_type(_RETRYABLE),
            reraise=True,
        ):
            with attempt:
                attempts += 1
                try:
                    response = await self._client.responses.create(
                        **self._build_kwargs(request, strict=strict)
                    )
                except BadRequestError as exc:
                    # A schema the API refuses is a permanent error; retrying
                    # in non-strict mode is the one useful recovery.
                    if strict and _is_schema_rejection(exc):
                        log.warning(
                            "llm.schema_rejected_falling_back",
                            purpose=request.purpose,
                            model=request.model,
                            error=str(exc)[:300],
                        )
                        strict = False
                        response = await self._client.responses.create(
                            **self._build_kwargs(request, strict=False)
                        )
                    else:
                        raise LLMError(
                            f"OpenAI rejected the request: {exc}",
                            detail={"purpose": request.purpose},
                            cause=exc,
                        ) from exc
                except APIStatusError as exc:
                    last_error = exc
                    if exc.status_code in (408, 409, 429) or exc.status_code >= 500:
                        raise  # let tenacity handle it
                    raise LLMError(
                        f"OpenAI returned {exc.status_code}.",
                        detail={"purpose": request.purpose},
                        cause=exc,
                    ) from exc

                text = self._extract_text(response)
                status = getattr(response, "status", "completed")
                incomplete = getattr(response, "incomplete_details", None)
                truncated = status == "incomplete"
                usage = self._usage_from(response)
                if truncated:
                    reason = getattr(incomplete, "reason", "unknown")
                    log.warning(
                        "llm.response_incomplete",
                        purpose=request.purpose,
                        reason=reason,
                        model=request.model,
                        output_tokens=usage.output_tokens,
                        reasoning_tokens=usage.reasoning_tokens,
                        content_tokens=usage.output_tokens - usage.reasoning_tokens,
                    )
                if not text.strip():
                    if truncated:
                        # The budget was spent before a single visible token was
                        # written -- on a reasoning model that means reasoning
                        # consumed all of it. Reported as truncation, not as a
                        # mystery empty reply, because the remedy is a bigger
                        # output budget or a smaller request.
                        raise LLMTruncatedError(
                            f"Model call '{request.purpose}' produced no visible output: "
                            f"all {usage.output_tokens} output tokens went to reasoning "
                            f"before the answer began.",
                            detail={
                                "purpose": request.purpose,
                                "reason": getattr(incomplete, "reason", "unknown"),
                                "output_tokens": usage.output_tokens,
                                "reasoning_tokens": usage.reasoning_tokens,
                                "max_output_tokens": (
                                    request.max_output_tokens or settings.llm_max_output_tokens
                                ),
                            },
                        )
                    raise LLMError(
                        "OpenAI returned an empty response.",
                        detail={"purpose": request.purpose, "status": status},
                    )

                return LLMResponse(
                    text=text,
                    model=getattr(response, "model", request.model),
                    usage=usage,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    provider=self.name,
                    attempts=attempts,
                    truncated=truncated,
                )

        raise LLMError("OpenAI request failed after retries.", cause=last_error)  # pragma: no cover

    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        if not texts:
            return []
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.llm_max_retries),
            wait=wait_exponential_jitter(initial=1.0, max=20.0),
            retry=retry_if_exception_type(_RETRYABLE),
            reraise=True,
        ):
            with attempt:
                kwargs: dict[str, Any] = {"model": model, "input": texts}
                if model.startswith("text-embedding-3"):
                    kwargs["dimensions"] = dimensions
                response = await self._client.embeddings.create(**kwargs)
                return [item.embedding for item in response.data]
        return []  # pragma: no cover

    async def aclose(self) -> None:
        await self._client.close()


def _is_schema_rejection(exc: BadRequestError) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in ("schema", "json_schema", "response_format", "unsupported", "strict")
    )
