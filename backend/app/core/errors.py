"""Application-level exception hierarchy.

Every error surfaced to a client derives from :class:`BioIntelError` and maps
to a stable machine-readable ``code`` plus an HTTP status.  Internal details
are never leaked; use ``detail`` for anything safe to show the caller and rely
on logs for the rest.
"""

from __future__ import annotations

from typing import Any


class BioIntelError(Exception):
    """Base class for all application errors."""

    code: str = "internal_error"
    status_code: int = 500
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.detail = detail or {}
        super().__init__(self.message)
        if cause is not None:
            self.__cause__ = cause

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


# --------------------------------------------------------------- client ---
class ValidationFailed(BioIntelError):
    code = "validation_failed"
    status_code = 422
    message = "The request payload failed validation."


class NotFound(BioIntelError):
    code = "not_found"
    status_code = 404
    message = "The requested resource does not exist."


class Conflict(BioIntelError):
    code = "conflict"
    status_code = 409
    message = "The request conflicts with the current state of the resource."


class Unauthorized(BioIntelError):
    code = "unauthorized"
    status_code = 401
    message = "Missing or invalid API credentials."


class RateLimited(BioIntelError):
    code = "rate_limited"
    status_code = 429
    message = "Too many requests. Please retry later."


class UnsupportedDocument(BioIntelError):
    code = "unsupported_document"
    status_code = 415
    message = "The uploaded file is not a supported PDF document."


class DocumentTooLarge(BioIntelError):
    code = "document_too_large"
    status_code = 413
    message = "The uploaded document exceeds the configured size limit."


# --------------------------------------------------------------- server ---
class PdfParseError(BioIntelError):
    code = "pdf_parse_error"
    status_code = 422
    message = "The PDF could not be parsed."


class LLMError(BioIntelError):
    code = "llm_error"
    status_code = 502
    message = "The language model provider returned an error."


class LLMSchemaError(LLMError):
    code = "llm_schema_error"
    message = "The language model returned output that did not satisfy the schema."


class LLMTruncatedError(LLMSchemaError):
    """The model ran out of output budget mid-JSON.

    Distinct from a schema violation because the remedy is different: resending
    the same prompt produces the same truncation.  The caller must ask for less
    (a smaller chunk) rather than ask again.
    """

    code = "llm_truncated"
    message = "The language model's response was cut off before the JSON was complete."


class LLMInputTooLarge(LLMError):
    """A request exceeded the per-call input budget before it was sent."""

    code = "llm_input_too_large"
    message = "The prompt exceeded the per-call input-token budget."


class LLMBudgetExceeded(LLMError):
    code = "llm_budget_exceeded"
    message = "The analysis exceeded its language-model call budget."


class RetrievalError(BioIntelError):
    code = "retrieval_error"
    status_code = 502
    message = "A literature source could not be reached."


class PipelineError(BioIntelError):
    code = "pipeline_error"
    status_code = 500
    message = "The analysis pipeline failed."
