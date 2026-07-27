"""Provider-agnostic LLM interface."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass(slots=True)
class ImagePart:
    """An image attached to a request (page render for vision reading)."""

    data: bytes
    mime_type: str = "image/png"
    detail: str = "high"

    def to_data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass(slots=True)
class LLMRequest:
    """A single structured-output request."""

    purpose: str
    system: str
    user: str
    schema_name: str
    schema_model: type[BaseModel]
    model: str
    images: list[ImagePart] = field(default_factory=list)
    max_output_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    #: Free-form metadata for logging (stage, page number, claim id...).
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponse:
    """Raw provider response plus accounting."""

    text: str
    model: str
    usage: Usage
    latency_ms: int
    provider: str
    attempts: int = 1
    truncated: bool = False
    raw: dict[str, Any] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Minimum surface every provider must implement."""

    name: str

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        """Return a JSON string conforming to ``request.schema_model``."""
        ...

    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...

    async def aclose(self) -> None: ...
