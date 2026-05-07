"""Error helpers — centralized, structured HTTPException responses.

The project standard is to return errors as `{"detail": {"error_type": str, "message": str}}`.
These helpers enforce that shape consistently across routes and avoid leaking
raw exception strings (which can carry stack traces, file paths, or upstream
internals) to clients.

The `ENDPOINT_HANDLED_EXCEPTIONS` and `STORAGE_HANDLED_EXCEPTIONS` tuples
collect every exception type endpoint handlers explicitly catch and convert
into a structured 500. Anything outside these tuples bubbles up to FastAPI's
default exception handler (which still produces JSON, just without our
structured detail). When a recurring exception type starts leaking through,
add it here — never widen a catch to bare `Exception`.

Note: Pydantic `ValidationError` is intentionally NOT in these tuples. Routes
that accept JSON-derived structured input handle it explicitly with a 422
response (see `agent/concept.py`, `agent/pipe_spec.py`).
"""

from typing import NoReturn

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from pipelex import log
from pipelex.base_exceptions import PipelexError
from temporalio.exceptions import TemporalError

from api.error_types import ErrorType

# Exceptions every pipeline / build / agent endpoint can legitimately
# encounter when calling into the pipelex runtime.
#
#   PipelexError    — base class for all pipelex domain errors. Covers
#                     ValidateBundleError, PipelexInterpreterError, CogtError
#                     (LLM/inference), PipeExecutionError, PipelineExecutionError,
#                     PipeNotFoundError, ToolError, etc.
#   TemporalError   — base class for temporalio errors (RPCError, ApplicationError).
#                     Raised by `runner.start_pipeline()` when Temporal connectivity
#                     or workflow dispatch fails. Does NOT inherit from PipelexError.
#   ValueError      — Pydantic JSON coercion, builder input validation,
#                     LLMPromptBlueprintValueError (a plain ValueError subclass).
#   TypeError       — pipe operator factories raise on bad blueprint shapes,
#                     e.g. ConstructFieldBlueprintTypeError.
#   RuntimeError    — image generation worker race / state errors in pipelex.
ENDPOINT_HANDLED_EXCEPTIONS: tuple[type[Exception], ...] = (
    PipelexError,
    TemporalError,
    ValueError,
    TypeError,
    RuntimeError,
)

# Storage backends (local FS, S3) layer additional exception types on top of
# the pipelex domain. Used by `/upload` and `/resolve-storage-url`.
#   PipelexError    — pipelex storage providers wrap most backend errors into
#                     StorageError → ToolError → PipelexError.
#   OSError         — local-FS provider raises FileNotFoundError, PermissionError,
#                     ConnectionError, etc., all OSError subclasses.
#   BotoCoreError /
#   ClientError     — defensive: should be wrapped by the pipelex S3 provider,
#                     but listed here so accidental leakage never hits FastAPI's
#                     default 500.
STORAGE_HANDLED_EXCEPTIONS: tuple[type[Exception], ...] = (
    PipelexError,
    OSError,
    BotoCoreError,
    ClientError,
)


def raise_internal_error(exc: BaseException, context: str) -> NoReturn:
    """Convert an arbitrary exception into a 500 with a structured detail.

    Logs the full traceback through the pipelex logger so configured
    formatting / sinks are honored. Returns only the exception class name and
    a short, generic message to the client. The caller's `context` is logged
    but NOT exposed in the response.
    """
    log.error(f"{context}: {type(exc).__name__}", include_exception=True)
    raise HTTPException(
        status_code=500,
        detail={
            "error_type": type(exc).__name__,
            "message": "Internal server error",
        },
    ) from exc


def raise_validation_error(message: str, error_type: ErrorType = ErrorType.VALIDATION_ERROR) -> NoReturn:
    """Raise a 422 with structured detail."""
    raise HTTPException(
        status_code=422,
        detail={
            "error_type": error_type,
            "message": message,
        },
    )


def raise_bad_request(message: str, error_type: ErrorType = ErrorType.BAD_REQUEST) -> NoReturn:
    """Raise a 400 with structured detail."""
    raise HTTPException(
        status_code=400,
        detail={
            "error_type": error_type,
            "message": message,
        },
    )


def raise_payload_too_large(message: str) -> NoReturn:
    """Raise a 413 with structured detail."""
    raise HTTPException(
        status_code=413,
        detail={
            "error_type": ErrorType.PAYLOAD_TOO_LARGE,
            "message": message,
        },
    )
