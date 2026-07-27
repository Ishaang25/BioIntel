"""The single entry point every pipeline stage uses to talk to a model.

Responsibilities that belong here rather than in a provider:

* **Validation.** Provider output is parsed into the requesting Pydantic model.
  A malformed payload triggers one repair round-trip before failing.
* **Budget.** A hard per-run ceiling on calls prevents a pathological document
  from producing an unbounded bill.
* **Concurrency.** A semaphore bounds parallel calls so a 200-page deck does
  not open 200 sockets.
* **Observability.** Every call is written to ``llm_call_logs`` with tokens,
  latency and estimated cost, keyed by run and stage.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.errors import LLMBudgetExceeded, LLMError, LLMSchemaError
from app.core.logging import get_logger
from app.db.models import LLMCallLog
from app.db.session import session_scope
from app.llm.base import ImagePart, LLMProvider, LLMRequest, LLMResponse, Usage
from app.llm.pricing import estimate_cost_usd
from app.llm.stub_provider import StubProvider

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass(slots=True)
class LLMMetrics:
    calls: int = 0
    failed_calls: int = 0
    repairs: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    by_purpose: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "repairs": self.repairs,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "reasoning_tokens": self.usage.reasoning_tokens,
            "cached_input_tokens": self.usage.cached_input_tokens,
            "estimated_cost_usd": round(self.cost_usd, 6),
            "by_purpose": dict(self.by_purpose),
        }


def build_provider() -> LLMProvider:
    """Instantiate the configured provider, degrading to the stub on failure."""
    if settings.llm_provider == "openai":
        from app.llm.openai_provider import OpenAIProvider

        try:
            return OpenAIProvider()
        except LLMError as exc:  # missing key, bad base URL...
            log.error("llm.provider_unavailable_falling_back", error=str(exc))
            return StubProvider()
    return StubProvider()


class LLMClient:
    """Facade over a provider, scoped to a single analysis run."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        run_id: str | None = None,
        max_calls: int | None = None,
        concurrency: int | None = None,
        persist_logs: bool = True,
    ) -> None:
        self.provider = provider or build_provider()
        self.run_id = run_id
        self.max_calls = max_calls if max_calls is not None else settings.llm_max_calls_per_run
        self.metrics = LLMMetrics()
        self._semaphore = asyncio.Semaphore(concurrency or settings.llm_concurrency)
        self._persist_logs = persist_logs
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- public ---
    @property
    def is_degraded(self) -> bool:
        """True when responses come from the offline analyser, not a model."""
        return getattr(self.provider, "name", "") == "stub"

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "name", "unknown")

    async def structured(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        model: str | None = None,
        images: list[ImagePart] | None = None,
        context: dict[str, Any] | None = None,
        stage: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ) -> T:
        """Run one structured-output call and return a validated model."""
        await self._reserve_budget(purpose)

        request = LLMRequest(
            purpose=purpose,
            system=system,
            user=user,
            schema_name=_schema_name(schema),
            schema_model=schema,
            model=model or settings.model_reasoning,
            images=images or [],
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            context=context or {},
        )

        async with self._semaphore:
            response = await self._call(request, stage=stage)

        try:
            return self._parse(response.text, schema)
        except LLMSchemaError as exc:
            log.warning(
                "llm.schema_violation_repairing",
                purpose=purpose,
                error=str(exc),
                snippet=response.text[:400],
            )
            async with self._lock:
                self.metrics.repairs += 1
            repaired = await self._repair(request, response.text, str(exc), stage=stage)
            return self._parse(repaired.text, schema)

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        model = model or settings.model_embedding
        async with self._semaphore:
            vectors = await self.provider.embed(
                texts, model=model, dimensions=settings.embedding_dimensions
            )
        async with self._lock:
            self.metrics.calls += 1
            self.metrics.by_purpose["embed"] = self.metrics.by_purpose.get("embed", 0) + 1
        return vectors

    async def gather(self, coros: list[Any], *, return_exceptions: bool = True) -> list[Any]:
        """Run awaitables concurrently; the semaphore bounds real parallelism."""
        return await asyncio.gather(*coros, return_exceptions=return_exceptions)

    async def aclose(self) -> None:
        await self.provider.aclose()

    # ------------------------------------------------------------ internal ---
    async def _reserve_budget(self, purpose: str) -> None:
        async with self._lock:
            if self.max_calls and self.metrics.calls >= self.max_calls:
                raise LLMBudgetExceeded(
                    f"Run exceeded its budget of {self.max_calls} model calls.",
                    detail={"purpose": purpose, "calls": self.metrics.calls},
                )

    async def _call(self, request: LLMRequest, *, stage: str | None) -> LLMResponse:
        try:
            response = await self.provider.complete_structured(request)
        except Exception as exc:
            async with self._lock:
                self.metrics.calls += 1
                self.metrics.failed_calls += 1
            self._log_call(request, None, stage=stage, error=str(exc)[:1000])
            log.error(
                "llm.call_failed",
                purpose=request.purpose,
                model=request.model,
                error=str(exc)[:500],
            )
            if isinstance(exc, LLMError):
                raise
            raise LLMError(f"Model call '{request.purpose}' failed: {exc}", cause=exc) from exc

        cost = estimate_cost_usd(response.model, response.usage)
        async with self._lock:
            self.metrics.calls += 1
            self.metrics.usage = self.metrics.usage + response.usage
            self.metrics.cost_usd += cost
            self.metrics.by_purpose[request.purpose] = (
                self.metrics.by_purpose.get(request.purpose, 0) + 1
            )
        self._log_call(request, response, stage=stage, cost=cost)
        log.debug(
            "llm.call",
            purpose=request.purpose,
            model=response.model,
            latency_ms=response.latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return response

    async def _repair(
        self, request: LLMRequest, bad_output: str, error: str, *, stage: str | None
    ) -> LLMResponse:
        await self._reserve_budget(f"{request.purpose}:repair")
        repair_request = LLMRequest(
            purpose=f"{request.purpose}:repair",
            system=request.system,
            user=(
                f"{request.user}\n\n"
                "---\n"
                "Your previous response did not satisfy the required schema.\n"
                f"Validation error:\n{error}\n\n"
                "Previous response:\n"
                f"{bad_output[:4000]}\n\n"
                "Return corrected JSON that satisfies the schema exactly. "
                "Do not add commentary."
            ),
            schema_name=request.schema_name,
            schema_model=request.schema_model,
            model=request.model,
            images=request.images,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            reasoning_effort=request.reasoning_effort,
            context=request.context,
        )
        async with self._semaphore:
            return await self._call(repair_request, stage=stage)

    @staticmethod
    def _parse(text: str, schema: type[T]) -> T:
        payload = _extract_json(text)
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise LLMSchemaError(
                f"Response failed {schema.__name__} validation: {_summarise_errors(exc)}",
                cause=exc,
            ) from exc

    def _log_call(
        self,
        request: LLMRequest,
        response: LLMResponse | None,
        *,
        stage: str | None,
        error: str | None = None,
        cost: float = 0.0,
    ) -> None:
        if not self._persist_logs:
            return
        try:
            with session_scope() as session:
                session.add(
                    LLMCallLog(
                        run_id=self.run_id,
                        stage=stage,
                        purpose=request.purpose[:64],
                        model=(response.model if response else request.model)[:64],
                        provider=self.provider_name,
                        input_tokens=response.usage.input_tokens if response else 0,
                        output_tokens=response.usage.output_tokens if response else 0,
                        reasoning_tokens=response.usage.reasoning_tokens if response else 0,
                        cached_input_tokens=response.usage.cached_input_tokens if response else 0,
                        latency_ms=response.latency_ms if response else 0,
                        attempts=response.attempts if response else 1,
                        ok=error is None,
                        error=error,
                        estimated_cost_usd=cost,
                    )
                )
        except Exception:  # telemetry must never break the pipeline
            log.warning("llm.call_log_write_failed", purpose=request.purpose, exc_info=True)


# ------------------------------------------------------------------ helpers ---
def _schema_name(schema: type[BaseModel]) -> str:
    """OpenAI requires ``^[a-zA-Z0-9_-]+$`` for the schema name."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", schema.__name__)


def _extract_json(text: str) -> Any:
    """Parse JSON, tolerating code fences and leading prose."""
    candidate = _JSON_FENCE_RE.sub("", text.strip())
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMSchemaError(f"Response was not valid JSON: {exc}", cause=exc) from exc
    raise LLMSchemaError("Response contained no JSON object.")


def _summarise_errors(exc: ValidationError, limit: int = 6) -> str:
    parts = []
    for error in exc.errors()[:limit]:
        location = ".".join(str(p) for p in error["loc"])
        parts.append(f"{location}: {error['msg']}")
    extra = len(exc.errors()) - limit
    if extra > 0:
        parts.append(f"(+{extra} more)")
    return "; ".join(parts)
