"""The single entry point every pipeline stage uses to talk to a model.

Responsibilities that belong here rather than in a provider:

* **Validation.** Provider output is parsed into the requesting Pydantic model.
  A malformed payload triggers one repair round-trip before failing; a payload
  that was *cut off* is salvaged locally instead, because resending the same
  prompt only reproduces the same truncation.
* **Budget.** A hard per-run ceiling on calls prevents a pathological document
  from producing an unbounded bill, and a per-call input ceiling keeps any one
  request small enough to answer quickly.
* **Concurrency.** A semaphore bounds parallel calls so a 200-page deck does
  not open 200 sockets.
* **Observability.** Every call is written to ``llm_call_logs`` and to the
  structured log with input/output tokens, latency, retries, truncation and
  estimated cost, keyed by run, stage and purpose.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.errors import (
    LLMBudgetExceeded,
    LLMError,
    LLMInputTooLarge,
    LLMSchemaError,
    LLMTruncatedError,
)
from app.core.logging import get_logger
from app.db.models import LLMCallLog
from app.db.session import session_scope
from app.llm.base import ImagePart, LLMProvider, LLMRequest, LLMResponse, Usage
from app.llm.json_repair import salvage_json
from app.llm.pricing import CostBreakdown, cost_breakdown
from app.llm.stub_provider import StubProvider
from app.utils.text import token_estimate

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

#: Rough input-token cost of one attached page render, for budget estimates.
IMAGE_TOKEN_ESTIMATE = 1_100


@dataclass(slots=True)
class StageUsage:
    """Per-stage roll-up, so a slow stage can be attributed to its calls."""

    calls: int = 0
    failed_calls: int = 0
    repairs: int = 0
    salvaged: int = 0
    truncated: int = 0
    provider_retries: int = 0
    latency_ms: int = 0
    max_latency_ms: int = 0
    input_tokens: int = 0
    max_input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "repairs": self.repairs,
            "salvaged": self.salvaged,
            "truncated": self.truncated,
            "provider_retries": self.provider_retries,
            "latency_ms_total": self.latency_ms,
            "latency_ms_max": self.max_latency_ms,
            "input_tokens": self.input_tokens,
            "input_tokens_max": self.max_input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "estimated_cost_usd": round(self.cost_usd, 6),
        }


@dataclass(slots=True)
class LLMMetrics:
    calls: int = 0
    failed_calls: int = 0
    repairs: int = 0
    #: Responses cut off by the output-token budget.
    truncated: int = 0
    #: Truncated responses recovered as partial results rather than lost.
    salvaged: int = 0
    #: Retries performed inside the provider (rate limits, timeouts, 5xx).
    provider_retries: int = 0
    #: Calls whose prompt exceeded the per-call input budget.
    over_input_budget: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    #: Same spend as ``cost_usd``, split into the components the provider
    #: bills separately. Accumulated per call so the split reflects the real
    #: per-model prices rather than being apportioned from the total.
    cost: CostBreakdown = field(default_factory=CostBreakdown)
    latency_ms: int = 0
    max_input_tokens_seen: int = 0
    by_purpose: dict[str, int] = field(default_factory=dict)
    by_stage: dict[str, StageUsage] = field(default_factory=dict)

    def stage(self, stage: str | None) -> StageUsage:
        return self.by_stage.setdefault(stage or "unattributed", StageUsage())

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "repairs": self.repairs,
            "truncated": self.truncated,
            "salvaged": self.salvaged,
            "provider_retries": self.provider_retries,
            "over_input_budget": self.over_input_budget,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "reasoning_tokens": self.usage.reasoning_tokens,
            "cached_input_tokens": self.usage.cached_input_tokens,
            "max_input_tokens_seen": self.max_input_tokens_seen,
            "latency_ms_total": self.latency_ms,
            "estimated_cost_usd": round(self.cost_usd, 6),
            "estimated_cost_breakdown_usd": {
                "input": round(self.cost.input_usd, 6),
                "cached_input": round(self.cost.cached_input_usd, 6),
                "output": round(self.cost.output_usd, 6),
                "reasoning": round(self.cost.reasoning_usd, 6),
            },
            "by_purpose": dict(self.by_purpose),
            "by_stage": {name: usage.to_dict() for name, usage in self.by_stage.items()},
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
        max_input_tokens: int | None = None,
        enforce_input_budget: bool = False,
    ) -> T:
        """Run one structured-output call and return a validated model.

        ``max_input_tokens`` (default :attr:`Settings.llm_max_input_tokens`) is
        checked before the request leaves the process.  Callers that control
        their own prompt size -- the chunked extraction stages -- pass
        ``enforce_input_budget=True`` so an oversized prompt is a bug that
        fails fast rather than a slow, expensive call.
        """
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
        await self._check_input_budget(
            request,
            stage=stage,
            limit=max_input_tokens or settings.llm_max_input_tokens,
            enforce=enforce_input_budget,
        )

        async with self._semaphore:
            response = await self._call(request, stage=stage)

        try:
            return self._parse(response.text, schema)
        except LLMSchemaError as exc:
            recovered = await self._recover(response, schema, purpose=purpose, stage=stage)
            if recovered is not None:
                return recovered
            if response.truncated:
                # The prompt is not the problem -- the answer did not fit. A
                # repair round-trip would truncate at exactly the same point,
                # so surface it and let the caller send a smaller chunk.
                raise LLMTruncatedError(
                    f"Model call '{purpose}' was cut off by the output-token limit "
                    f"({response.usage.output_tokens} output tokens) and could not be "
                    "salvaged; the request needs to be split.",
                    detail={"purpose": purpose, "output_tokens": response.usage.output_tokens},
                    cause=exc,
                ) from exc
            log.warning(
                "llm.schema_violation_repairing",
                purpose=purpose,
                stage=stage,
                error=str(exc),
                snippet=response.text[:400],
            )
            async with self._lock:
                self.metrics.repairs += 1
                self.metrics.stage(stage).repairs += 1
            repaired = await self._repair(request, response.text, str(exc), stage=stage)
            try:
                return self._parse(repaired.text, schema)
            except LLMSchemaError as repair_exc:
                salvaged = await self._recover(repaired, schema, purpose=purpose, stage=stage)
                if salvaged is not None:
                    return salvaged
                raise repair_exc

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

    async def _check_input_budget(
        self,
        request: LLMRequest,
        *,
        stage: str | None,
        limit: int,
        enforce: bool,
    ) -> None:
        """Reject or flag a prompt that is too large before paying for it.

        The estimate is deliberately cheap (~4 chars/token) -- it exists to
        catch the order-of-magnitude mistake of handing a whole deck to one
        call, not to predict the provider's tokenizer.
        """
        estimated = estimated_input_tokens(request)
        if estimated <= limit:
            return

        async with self._lock:
            self.metrics.over_input_budget += 1
        log.warning(
            "llm.input_budget_exceeded",
            purpose=request.purpose,
            stage=stage,
            model=request.model,
            estimated_input_tokens=estimated,
            limit=limit,
            enforced=enforce,
        )
        if enforce:
            raise LLMInputTooLarge(
                f"Model call '{request.purpose}' would send about {estimated} input tokens, "
                f"over the {limit}-token per-call budget; split the input.",
                detail={
                    "purpose": request.purpose,
                    "estimated_input_tokens": estimated,
                    "limit": limit,
                },
            )

    async def _recover(
        self,
        response: LLMResponse,
        schema: type[T],
        *,
        purpose: str,
        stage: str | None,
    ) -> T | None:
        """Rebuild a validated model from a response that was cut off.

        A truncated list of entities is still a list of entities. Recovering it
        locally is free, whereas re-asking costs another full round-trip and
        hits the same ceiling.
        """
        payload = salvage_json(response.text)
        if payload is None:
            return None
        try:
            model = schema.model_validate(payload)
        except ValidationError:
            return None

        async with self._lock:
            self.metrics.salvaged += 1
            self.metrics.stage(stage).salvaged += 1
        log.warning(
            "llm.truncated_output_salvaged",
            purpose=purpose,
            stage=stage,
            output_tokens=response.usage.output_tokens,
            response_chars=len(response.text),
            detail=(
                "The response exceeded its output-token budget; the complete part was kept "
                "and the incomplete trailing element discarded."
            ),
        )
        return model

    async def _call(self, request: LLMRequest, *, stage: str | None) -> LLMResponse:
        try:
            response = await self.provider.complete_structured(request)
        except Exception as exc:
            async with self._lock:
                self.metrics.calls += 1
                self.metrics.failed_calls += 1
                usage = self.metrics.stage(stage)
                usage.calls += 1
                usage.failed_calls += 1
            self._log_call(request, None, stage=stage, error=str(exc)[:1000])
            log.error(
                "llm.call_failed",
                purpose=request.purpose,
                stage=stage,
                model=request.model,
                error=str(exc)[:500],
            )
            if isinstance(exc, LLMError):
                raise
            raise LLMError(f"Model call '{request.purpose}' failed: {exc}", cause=exc) from exc

        breakdown = cost_breakdown(response.model, response.usage)
        cost = round(breakdown.total_usd, 6)
        retries = max(0, response.attempts - 1)
        async with self._lock:
            self.metrics.calls += 1
            self.metrics.usage = self.metrics.usage + response.usage
            self.metrics.cost_usd += cost
            self.metrics.cost = self.metrics.cost + breakdown
            self.metrics.latency_ms += response.latency_ms
            self.metrics.provider_retries += retries
            self.metrics.max_input_tokens_seen = max(
                self.metrics.max_input_tokens_seen, response.usage.input_tokens
            )
            if response.truncated:
                self.metrics.truncated += 1
            self.metrics.by_purpose[request.purpose] = (
                self.metrics.by_purpose.get(request.purpose, 0) + 1
            )
            usage = self.metrics.stage(stage)
            usage.calls += 1
            usage.provider_retries += retries
            usage.latency_ms += response.latency_ms
            usage.max_latency_ms = max(usage.max_latency_ms, response.latency_ms)
            usage.input_tokens += response.usage.input_tokens
            usage.max_input_tokens = max(usage.max_input_tokens, response.usage.input_tokens)
            usage.output_tokens += response.usage.output_tokens
            usage.cached_input_tokens += response.usage.cached_input_tokens
            usage.reasoning_tokens += response.usage.reasoning_tokens
            usage.cost_usd += cost
            if response.truncated:
                usage.truncated += 1

        self._log_call(request, response, stage=stage, cost=cost)
        # Every model call is a line in the timing profile: this is the record
        # that turns "the entities stage is slow" into "one call, 542 seconds,
        # 21,871 input tokens, output capped at 16,000".
        log.info(
            "llm.call",
            purpose=request.purpose,
            stage=stage,
            model=response.model,
            latency_ms=response.latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            reasoning_tokens=response.usage.reasoning_tokens,
            cached_input_tokens=response.usage.cached_input_tokens,
            retries=retries,
            truncated=response.truncated,
            estimated_cost_usd=round(cost, 6),
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


def estimated_input_tokens(request: LLMRequest) -> int:
    """Cheap pre-send estimate of a request's input size."""
    return (
        token_estimate(request.system)
        + token_estimate(request.user)
        + IMAGE_TOKEN_ESTIMATE * len(request.images)
    )


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
            # Do not report the offset of the last '}' as the failure point --
            # for a truncated document that brace belongs to a nested object
            # and the message misleads. The caller retries salvage.
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
