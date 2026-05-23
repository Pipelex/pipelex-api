"""FastAPI app init, middleware, router registration, and global error handlers.

The app-level exception handlers registered here are the single place the API
translates a failure into an HTTP response, and every one emits the same RFC
7807 `application/problem+json` shape. An `ApiError` (raised by the `api.errors`
4xx/5xx helpers) carries a pre-built problem document; a FastAPI
`RequestValidationError` from automatic request validation is rendered into
one; every `PipelexError` is turned into a problem document built from its
`ErrorReport`; bare `temporalio` transport errors get an API-authored
classification; everything else collapses to a sanitized 500. Routes therefore
no longer need to catch and shape errors themselves.
"""

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pipelex import log
from pipelex.base_exceptions import ErrorDomain, ErrorReport, PipelexError
from pipelex.pipelex import Pipelex
from pipelex.system.environment import get_optional_env
from pipelex.system.runtime import IntegrationMode
from starlette.middleware.base import BaseHTTPMiddleware
from temporalio.exceptions import TemporalError

from api.disclosure import resolve_disclosure_mode
from api.error_types import ErrorType
from api.error_uri import error_type_uri
from api.errors import ApiError
from api.middleware import RequestIdMiddleware, request_body_size_middleware
from api.problem_document import PROBLEM_JSON_MEDIA_TYPE, build_problem_document, build_problem_document_from_api_error
from api.routes import router as api_router
from api.routes.health import router as health_router
from api.security import get_auth_dependency


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    Pipelex.make(integration_mode=IntegrationMode.FASTAPI)
    try:
        yield
    finally:
        Pipelex.teardown_if_needed()


def _resolve_cors_origins() -> tuple[list[str], bool]:
    """Read CORS_ALLOW_ORIGINS env var. Returns (origins, allow_credentials).

    Default: wildcard origins, credentials disabled — the only valid combination
    when origins is `*` (browsers reject credentials with wildcard). To enable
    credentials, set CORS_ALLOW_ORIGINS to a comma-separated allowlist.
    """
    raw = get_optional_env("CORS_ALLOW_ORIGINS")
    if not raw or raw.strip() == "*":
        return ["*"], False
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if not origins:
        return ["*"], False
    return origins, True


# Resolve and validate ERROR_DISCLOSURE once, at import/startup: an unrecognized
# value raises here and the app fails to boot rather than silently defaulting.
# The resolved mode drives how much of an error report reaches a client. The
# exception handlers below read this module global at call time, so a test can
# override it via `mocker.patch("api.main.ERROR_DISCLOSURE_MODE", ...)`.
ERROR_DISCLOSURE_MODE = resolve_disclosure_mode()

fastapi_app = FastAPI(redirect_slashes=False, lifespan=lifespan)

cors_origins, cors_allow_credentials = _resolve_cors_origins()
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)
fastapi_app.add_middleware(BaseHTTPMiddleware, dispatch=request_body_size_middleware)

fastapi_app.include_router(health_router)

# Register all other routes WITH authentication (auto-selects based on AUTH_MODE env var: none/jwt/api_key)
auth_dependency = get_auth_dependency()
fastapi_app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(auth_dependency)])


@fastapi_app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Pipelex API"}


# --- Global exception handlers ----------------------------------------------


def _request_id_of(request: Request) -> str | None:
    """Return the correlation id `RequestIdMiddleware` stored on the request.

    Read defensively with `getattr`: a request that never went through the
    middleware (a unit test, a non-HTTP scope) simply has no id.
    """
    return getattr(request.state, "request_id", None)


def _retry_after_header(report: ErrorReport) -> dict[str, str]:
    """Return a `Retry-After` header dict when a retry hint applies to this response.

    Emitted only when the response is itself a retry invitation — the
    provider-429 passthrough (`report.http_status == 429`) — so a hint never
    rides a 500/422 where `Retry-After` is meaningless. The provider's
    `retry_after_seconds` is rounded up and clamped to a non-negative integer;
    a non-finite value (a provider sending `Retry-After: inf` / `nan`) is
    dropped rather than crashing `math.ceil`. Empty dict when no usable hint
    applies.
    """
    if report.http_status != 429:
        return {}
    metadata = report.provider_metadata
    if metadata is None:
        return {}
    seconds = metadata.retry_after_seconds
    if seconds is None or not math.isfinite(seconds):
        return {}
    return {"Retry-After": str(max(0, math.ceil(seconds)))}


def _emit_error_log(*, fields: dict[str, Any], as_error: bool) -> None:
    """Emit one structured error-log line from a flat field map.

    The pipelex `log` object renders a single message string rather than
    indexed key/value fields, so the fields are flattened to a `key=value`
    run: greppable today, and a clean migration target once a JSON log sink
    lands. `None`-valued fields are dropped. `as_error` picks the level —
    `error` (with traceback) for operator-actionable failures, `warning` for
    `INPUT`-domain caller mistakes.
    """
    rendered = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    if as_error:
        log.error(rendered, include_exception=True)
    else:
        log.warning(rendered)


def _log_error_report(report: ErrorReport, *, request: Request, request_id: str | None) -> None:
    """Emit the structured log entry for a handled `ErrorReport`.

    `INPUT`-domain errors are the caller's mistake, not the operator's — they
    log at `warning` without a traceback. Everything else logs at `error` with
    the traceback. The fields mirror the response so the two never drift.
    """
    is_caller_error = report.error_domain == ErrorDomain.INPUT
    fields: dict[str, Any] = {
        "event": "api_error",
        "request_id": request_id,
        "route": request.url.path,
        "error_type": report.error_type,
        "error_category": report.error_category,
        "error_domain": report.error_domain,
        "retryable": report.retryable,
        "status": report.http_status,
        "provider": report.provider,
        "model": report.model,
    }
    metadata = report.provider_metadata
    if metadata is not None:
        fields["provider_status_code"] = metadata.status_code
        fields["provider_request_id"] = metadata.request_id
    _emit_error_log(fields=fields, as_error=not is_caller_error)


def _log_api_authored_error(*, document: dict[str, Any], status: int, request: Request, request_id: str | None) -> None:
    """Emit the structured log entry for an API-authored error response.

    Shares the disposition rule and the common-key set of `_log_error_report`
    so every error response — a pipelex `ErrorReport` translated to RFC 7807
    *or* an API-authored 4xx/5xx raised by an `api.errors` helper — produces
    one `event=api_error` line a downstream sink can grep uniformly on
    `event`, `request_id`, `route`, `error_type`, `error_domain`, `retryable`,
    and `status`. Without this, an API-owned 500 (a `raise_internal_server_error`
    site — `/pipelex_version`'s missing-package case is the canonical example)
    would land with zero operator output, since `handle_api_error` only
    serializes the response.

    Where the two helpers differ — by design, matching what each side actually
    has to log:
    - API-authored docs add `detail`, always safe because
      `build_problem_document_from_api_error` does not apply strict-disclosure
      redaction (only `build_problem_document` does for pipelex domain errors).
      Carrying the message preserves the operator-facing cause in the log line.
    - API-authored docs omit `error_category`, `provider`, `model`, and
      `provider_metadata.*` — those are inference-domain classifiers pipelex
      sets only on classifiable failures and the API never authors itself.

    `INPUT`-domain caller mistakes log at `warning` without a traceback;
    everything else logs at `error` with the traceback — same disposition rule
    `_log_error_report` uses, so a sink dedup'ing by level sees one shape.
    """
    error_domain = document.get("error_domain")
    is_caller_error = error_domain == ErrorDomain.INPUT
    fields: dict[str, Any] = {
        "event": "api_error",
        "request_id": request_id,
        "route": request.url.path,
        "error_type": document.get("error_type"),
        "error_domain": error_domain,
        "retryable": document.get("retryable"),
        "status": status,
        "detail": document.get("detail"),
    }
    _emit_error_log(fields=fields, as_error=not is_caller_error)


def _json_safe_report(report: ErrorReport) -> ErrorReport:
    """Return a report whose floats Starlette's JSON encoder can render.

    `JSONResponse` encodes with `allow_nan=False`, so a non-finite
    `provider_metadata.retry_after_seconds` — a provider sending `Retry-After:
    inf` / `nan`, which pipelex's header parser passes through unchecked —
    would crash the whole response render, not just the `Retry-After` header.
    Null the unusable hint out; the rest of the report renders intact.
    """
    metadata = report.provider_metadata
    if metadata is None:
        return report
    seconds = metadata.retry_after_seconds
    if seconds is None or math.isfinite(seconds):
        return report
    safe_metadata = metadata.model_copy(update={"retry_after_seconds": None})
    return report.model_copy(update={"provider_metadata": safe_metadata})


def _problem_response(report: ErrorReport, *, request: Request) -> JSONResponse:
    """Build the RFC 7807 `JSONResponse` for an `ErrorReport` and log the entry.

    Shared by the `PipelexError` and `TemporalError` handlers: both produce an
    `ErrorReport`, so both render and log it identically. The report is first
    made JSON-safe (see `_json_safe_report`); the status code then comes from
    `report.http_status` (which already encodes the provider-429 passthrough),
    and the response body from the Phase 1 problem-document builder under the
    startup-resolved disclosure mode.
    """
    report = _json_safe_report(report)
    request_id = _request_id_of(request)
    document = build_problem_document(
        report,
        instance=request.url.path,
        request_id=request_id,
        disclosure_mode=ERROR_DISCLOSURE_MODE,
    )
    _log_error_report(report, request=request, request_id=request_id)
    return JSONResponse(
        status_code=report.http_status,
        content=document,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        headers=_retry_after_header(report),
    )


async def handle_pipelex_error(request: Request, exc: Exception) -> Response:
    """Translate any pipelex `PipelexError` into an RFC 7807 problem response.

    The single place in the API that consumes an `ErrorReport`.
    `to_error_report()` walks the `__cause__` chain, so a wrapper exception
    still surfaces the classification of the underlying failure. `exc` is typed
    `Exception` to match Starlette's handler contract; FastAPI only routes a
    `PipelexError` here, so the cast is sound. `WorkflowExecutionError` — a
    Temporal workflow failure observed on the submitter side — is a
    `PipelexError` (via `TemporalFlowError`) and is unrelated to `temporalio`'s
    `TemporalError`, so Starlette's MRO walk resolves it here, never to the
    `TemporalError` handler.
    """
    report = cast("PipelexError", exc).to_error_report()
    return _problem_response(report, request=request)


async def handle_temporal_error(request: Request, exc: Exception) -> Response:
    """Translate a bare temporalio `TemporalError` into an RFC 7807 problem response.

    These are transport-level failures of the Temporal client — cluster
    unreachable, an RPC error during workflow dispatch — that surface before
    pipelex's `@convert_pipelex_errors` wrapping applies. pipelex deliberately
    does not classify them, so the API authors the `ErrorReport` itself: a
    retryable, `RUNTIME`-domain `transient` failure. (`WorkflowExecutionError`
    IS a `PipelexError`, so it is handled by `handle_pipelex_error` instead.)
    """
    report = ErrorReport(
        error_type="TemporalTransportError",
        message=str(exc),
        title="Temporal transport error",
        type_uri=error_type_uri("TemporalTransportError"),
        error_category="transient",
        error_domain=ErrorDomain.RUNTIME,
        retryable=True,
    )
    return _problem_response(report, request=request)


async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Catch-all for any failure that is neither a `PipelexError` nor a `TemporalError`.

    One of the two `except Exception`-equivalent sites the project sanctions —
    the outermost handler of an API. The response body is fully sanitized: no
    exception class name, no `str(exc)`, no traceback reaches the client. The
    real class name and a full traceback go to the operator log instead,
    correlated by request id. This handler also covers a failure *inside*
    another handler (e.g. a corrupt `to_error_report()`): Starlette's
    `ServerErrorMiddleware` wraps the others, so such a failure still lands a
    sanitized 500 rather than a bodyless default.
    """
    request_id = _request_id_of(request)
    _emit_error_log(
        fields={
            "event": "api_error",
            "request_id": request_id,
            "route": request.url.path,
            "error_type": type(exc).__name__,
            "error_category": "unknown",
            "error_domain": ErrorDomain.RUNTIME,
            "status": 500,
        },
        as_error=True,
    )
    document: dict[str, Any] = {
        "type": error_type_uri("InternalServerError"),
        "title": "Internal server error",
        "status": 500,
        "detail": "An unexpected error occurred. The request id is included for support.",
        "instance": request.url.path,
        "error_type": "InternalServerError",
        "error_domain": ErrorDomain.RUNTIME,
        "error_category": "unknown",
        "retryable": False,
    }
    if request_id is not None:
        document["request_id"] = request_id
    return JSONResponse(status_code=500, content=document, media_type=PROBLEM_JSON_MEDIA_TYPE)


async def handle_api_error(request: Request, exc: Exception) -> Response:
    """Render an API-authored `ApiError` as an RFC 7807 problem response.

    `ApiError` is raised by the `raise_*` helpers in `api.errors` for the API's
    own 4xx/5xx — request validation, auth, payload limits, misconfiguration.
    The problem document is built at raise time (under the request-scoped
    logging contextvars); this handler serializes it, re-attaches any
    `WWW-Authenticate` challenge header, and emits the structured `event=api_error`
    log line so an API-owned 500 from any route is observable (a
    `raise_internal_server_error` site like `version.py`'s missing-package case
    is the canonical example — it has no preceding `log.error` at the call
    site). `exc` is typed `Exception` to match Starlette's handler contract;
    FastAPI only routes an `ApiError` here, so the cast is sound.
    """
    api_error = cast("ApiError", exc)
    _log_api_authored_error(
        document=api_error.document,
        status=api_error.status_code,
        request=request,
        request_id=_request_id_of(request),
    )
    return JSONResponse(
        status_code=api_error.status_code,
        content=api_error.document,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        headers=api_error.headers,
    )


def _summarize_request_validation_error(exc: RequestValidationError) -> str:
    """Render FastAPI's per-field validation failures as one human-readable string.

    `RequestValidationError.errors()` is a list of per-field dicts; RFC 7807's
    `detail` is a single human-readable string, so each entry is rendered as
    `<location>: <message>` and the lot joined. Only `loc` and `msg` are read —
    both are plain strings / ints — so nothing unserializable from a crafted
    request body can reach the response.
    """
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()))
        message = error.get("msg") or "Invalid input"
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "Request validation failed"


async def handle_request_validation_error(request: Request, exc: Exception) -> Response:
    """Translate FastAPI's automatic request validation failure into RFC 7807.

    FastAPI raises `RequestValidationError` when a request body, query, or path
    parameter fails the endpoint's declared Pydantic model — an extra field
    under `extra="forbid"`, a `min_length` / `max_length` breach, a missing or
    mistyped field, a `field_validator` raising `ValueError`, malformed JSON. Its
    built-in handler would answer with a bare `{"detail": [...]}` /
    `application/json` body; registering this handler overrides that so an
    endpoint's caller-input rejection is the same `application/problem+json`
    shape as an explicit `raise_validation_error` — one error contract across
    every endpoint. `exc` is typed `Exception` to match Starlette's handler
    contract; FastAPI only routes a `RequestValidationError` here, so the cast
    is sound.
    """
    validation_error = cast("RequestValidationError", exc)
    request_id = _request_id_of(request)
    document = build_problem_document_from_api_error(
        ErrorType.VALIDATION_ERROR,
        _summarize_request_validation_error(validation_error),
        422,
        instance=request.url.path,
        request_id=request_id,
        error_domain=ErrorDomain.INPUT,
    )
    # Same `event=api_error` line as an explicit `raise_validation_error`,
    # so FastAPI's automatic-validation 422s aren't silent in operator logs.
    _log_api_authored_error(document=document, status=422, request=request, request_id=request_id)
    return JSONResponse(status_code=422, content=document, media_type=PROBLEM_JSON_MEDIA_TYPE)


def register_exception_handlers(app: FastAPI) -> None:
    """Register the app-level exception handlers on `app`.

    Resolution is most-specific-first: an API-authored `ApiError` →
    `handle_api_error`; a FastAPI `RequestValidationError` (automatic
    request-body / parameter validation) → `handle_request_validation_error`; a
    `PipelexError` (including `WorkflowExecutionError`) → `handle_pipelex_error`;
    a non-pipelex `TemporalError` → `handle_temporal_error`; anything else →
    `handle_unexpected_error`. Registering `RequestValidationError` overrides
    FastAPI's built-in handler so its automatic-validation failures answer in
    the same `application/problem+json` shape as every other error, not the
    default `{"detail": [...]}`. Shared by the production app below and by the
    unit tests, which register the same handlers on a throwaway app.
    """
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(PipelexError, handle_pipelex_error)
    app.add_exception_handler(TemporalError, handle_temporal_error)
    app.add_exception_handler(Exception, handle_unexpected_error)


register_exception_handlers(fastapi_app)


# RequestIdMiddleware wraps the *entire* FastAPI app — including Starlette's
# ServerErrorMiddleware, which `add_middleware` could only ever nest inside.
# This is what makes it genuinely outermost: the request-id contextvars are
# bound, and `X-Request-ID` is echoed, on every response — the catch-all 500
# included. `app` is the ASGI entrypoint (uvicorn loads `api.main:app`).
app = RequestIdMiddleware(fastapi_app)
