"""FastAPI app init, middleware, router registration, and global error handlers.

The three app-level exception handlers registered here are the single place
the API translates a failure into an HTTP response. Every `PipelexError` is
turned into an RFC 7807 problem document built from its `ErrorReport`; bare
`temporalio` transport errors get an API-authored classification; everything
else collapses to a sanitized 500. Routes therefore no longer need to catch
and shape errors themselves.
"""

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import Depends, FastAPI, Request, Response
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
from api.error_uri import error_type_uri
from api.middleware import RequestIdMiddleware, request_body_size_middleware
from api.problem_document import build_problem_document
from api.routes import router as api_router
from api.routes.health import router as health_router
from api.security import get_auth_dependency

# Content type for every error response — RFC 7807 problem documents.
PROBLEM_JSON_MEDIA_TYPE = "application/problem+json"


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
    """Return a `Retry-After` header dict when the report carries a provider retry hint.

    The provider's own `retry_after_seconds` is rounded up to whole seconds.
    This is the one HTTP-protocol nicety that does not fall out of the body
    alone — empty dict when there is no hint.
    """
    metadata = report.provider_metadata
    if metadata is not None and metadata.retry_after_seconds is not None:
        return {"Retry-After": str(math.ceil(metadata.retry_after_seconds))}
    return {}


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


def _problem_response(report: ErrorReport, *, request: Request) -> JSONResponse:
    """Build the RFC 7807 `JSONResponse` for an `ErrorReport` and log the entry.

    Shared by the `PipelexError` and `TemporalError` handlers: both produce an
    `ErrorReport`, so both render and log it identically. The status code comes
    from `report.http_status` (which already encodes the provider-429
    passthrough), and the response body from the Phase 1 problem-document
    builder under the startup-resolved disclosure mode.
    """
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
    `PipelexError` here, so the cast is sound. `WorkflowExecutionError` (a
    `PipelexError` raised on the submitter side of a Temporal workflow) is more
    specific than `temporalio`'s `TemporalError` and resolves here, not in the
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
        "error_type": "InternalServerError",
        "error_domain": ErrorDomain.RUNTIME,
        "retryable": False,
    }
    if request_id is not None:
        document["request_id"] = request_id
    return JSONResponse(status_code=500, content=document, media_type=PROBLEM_JSON_MEDIA_TYPE)


def register_exception_handlers(app: FastAPI) -> None:
    """Register the three app-level exception handlers on `app`.

    Resolution is most-specific-first: a `PipelexError` (including
    `WorkflowExecutionError`) → `handle_pipelex_error`; a non-pipelex
    `TemporalError` → `handle_temporal_error`; anything else →
    `handle_unexpected_error`. Shared by the production app below and by the
    Phase 2 unit tests, which register the same handlers on a throwaway app.
    """
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
