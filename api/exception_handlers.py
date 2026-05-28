"""Global FastAPI exception handlers that translate any failure into RFC 7807.

The single place the API renders a failure into an HTTP response. Every
handler produces the same `application/problem+json` shape: an `ApiError`
(raised by the `api.errors` 4xx/5xx helpers) carries a pre-built problem
document; a FastAPI `RequestValidationError` from automatic request
validation is rendered into one; every `PipelexError` is turned into a
problem document built from its `ErrorReport`; bare `temporalio` transport
errors get an API-authored classification; everything else collapses to a
sanitized 500. Routes therefore no longer need to catch and shape errors
themselves.

This module is deliberately import-side-effect-free — no env var reads, no
app construction. `api/main.py` calls `register_exception_handlers(app,
disclosure_mode=...)` at startup with the already-resolved disclosure mode
(its own fail-fast read is what keeps production strict on a bad
`ERROR_DISCLOSURE` value). Keeping the handlers in their own module lets
tests import `register_exception_handlers` without dragging in the
production app's startup chain (`Pipelex.make`, `get_auth_dependency`,
router wiring), so a misconfigured env var can't crash test collection of
every module that imports a handler at once.
"""

import math
import re
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pipelex import log
from pipelex.base_exceptions import DisclosureMode, ErrorDomain, ErrorReport, PipelexError
from temporalio.exceptions import TemporalError

from api.error_types import ErrorType
from api.error_uri import error_type_uri
from api.errors import ApiError
from api.problem_document import PROBLEM_JSON_MEDIA_TYPE, build_problem_document, build_problem_document_from_api_error

if TYPE_CHECKING:
    from api.security import RequestUser


def _request_id_of(request: Request) -> str | None:
    """Return the correlation id `RequestIdMiddleware` stored on the request.

    Read defensively with `getattr`: a request that never went through the
    middleware (a unit test, a non-HTTP scope) simply has no id.
    """
    return getattr(request.state, "request_id", None)


def _user_id_of(request: Request) -> str | None:
    """Return the authenticated caller's id, when one is on the request.

    `api.security._set_request_user` stores a `RequestUser` on
    `request.state.user` after a successful auth check (jwt, or
    `TRUST_FORWARDED_IDENTITY_HEADERS=true`). The global error-log sites read
    that here so an operator can tie a failure to a caller without grepping
    multiple log lines by `request_id` — and without each route having to log
    `user=<id>` itself before raising (which Phase 3 removed). Returns `None`
    pre-auth, on the static-API-key surface (no per-caller identity), or for
    `AUTH_MODE=none` without the forwarded-identity opt-in: `emit_error_log`
    drops `None`-valued fields, so the field is absent from the rendered line
    rather than `user_id=None`.
    """
    user: RequestUser | None = getattr(request.state, "user", None)
    return user.user_id if user is not None else None


def _pipe_code_of(request: Request) -> str | None:
    """Return the body-derived `pipe_code` when `_parse_request` bound one.

    `api.routes.pipelex.pipeline._parse_request` writes `pipe_code` onto
    `request.state` right after the body decodes (before
    `_validate_extras` / `from_body`), normalized through
    `_coerce_correlation_field` — empty / non-string / oversized inputs become
    `None` so a caller cannot inject a bare `pipe_code=` token or inflate the
    log line. Returns `None` for routes that don't use `_parse_request`, for
    requests whose body never decoded (`_decode_body` raised 422), and for
    bodies that legitimately omitted `pipe_code` (the `mthds_contents`-only
    invocation). `emit_error_log` drops `None`-valued fields.
    """
    return getattr(request.state, "pipe_code", None)


def _pipeline_run_id_of(request: Request) -> str | None:
    """Return the parsed `pipeline_run_id` when `_parse_request` bound one on the request.

    Same shape as `_pipe_code_of`. Source: the raw body's `pipeline_run_id`,
    normalized through `_coerce_correlation_field` at the binding site (empty
    string → `None`, oversized → truncated). Letting the field ride every
    error log lets an operator correlate the API-side failure with the
    worker-side traces that share the same run id, without each backend frame
    having to forward it.
    """
    return getattr(request.state, "pipeline_run_id", None)


def _request_correlation_fields(request: Request) -> dict[str, str | None]:
    """Return the request-scoped correlation fields every error log carries.

    Single source of truth for the `user_id` / `pipe_code` / `pipeline_run_id`
    field set so the three log paths (`_log_error_report`,
    `_log_api_authored_error`, `handle_unexpected_error`) cannot drift. Each
    value is `None` when the corresponding state is not bound on this request;
    `emit_error_log` drops `None`-valued fields, so unbound identifiers are
    absent from the rendered line rather than appearing as `pipe_code=None`.
    """
    return {
        "user_id": _user_id_of(request),
        "pipe_code": _pipe_code_of(request),
        "pipeline_run_id": _pipeline_run_id_of(request),
    }


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


_LOGFMT_NEEDS_QUOTING = re.compile(r'[\s"=]')


def _logfmt_value(value: Any) -> str:
    """Render a value for the ``key=value`` log format, escaping caller input.

    Several `emit_error_log` callers ship caller-controlled strings into the
    field map — `_log_api_authored_error` forwards `document["detail"]`
    (a validation message, a callback-URL rejection reason, etc.) and the
    catch-all handler ships `type(exc).__name__` which can be anything pipelex
    raised. Without escaping, a crafted body with a newline or whitespace
    inside `detail` would break the field separator (`key=value key2=value2`)
    or forge extra log fields (`detail=ok status=200 event=fake`) — both are
    log-injection vectors.

    Non-string values (`int`, `bool`, `StrEnum` instances) have no injection
    surface and render bare. Strings get logfmt-style treatment: control chars
    are backslash-escaped first, then values containing whitespace, `=`, or
    `"` are wrapped in double quotes with embedded `"` doubled. The shape
    survives both grep (the line stays single-line and the keys remain at the
    same offsets) and a future JSON log sink (each field is recoverable).
    """
    if not isinstance(value, str):
        return str(value)
    escaped = value.encode("unicode_escape").decode("ascii")
    if _LOGFMT_NEEDS_QUOTING.search(escaped):
        return '"' + escaped.replace('"', '""') + '"'
    return escaped


def emit_error_log(*, fields: dict[str, Any], as_error: bool) -> None:
    """Emit one structured error-log line from a flat field map.

    The pipelex `log` object renders a single message string rather than
    indexed key/value fields, so the fields are flattened to a `key=value`
    run: greppable today, and a clean migration target once a JSON log sink
    lands. `None`-valued fields are dropped. `as_error` picks the level —
    `error` (with traceback) for operator-actionable failures, `warning` for
    `INPUT`-domain caller mistakes. Caller-controlled values go through
    `_logfmt_value` so a crafted `detail` can't forge log fields.
    """
    rendered = " ".join(f"{key}={_logfmt_value(value)}" for key, value in fields.items() if value is not None)
    if as_error:
        log.error(rendered, include_exception=True)
    else:
        log.warning(rendered)


def _log_error_report(report: ErrorReport, *, request: Request, request_id: str | None, status: int | None = None) -> None:
    """Emit the structured log entry for a handled `ErrorReport`.

    `INPUT`-domain errors are the caller's mistake, not the operator's — they
    log at `warning` without a traceback. Everything else logs at `error` with
    the traceback. The fields mirror the response so the two never drift.
    `user_id` rides every line when auth bound a caller — without it, the
    storage / pipeline-backend leg of a failure carries only `request_id` and
    `route`, and tying the failure to the caller requires correlating the
    request id across unrelated log lines (Phase 3 deleted the per-route
    `log.error(... user=...)` lines those failures used to emit).

    ``status`` defaults to ``report.http_status`` and exists so the caller can
    pass the post-override value (see ``_ERROR_TYPE_STATUS_OVERRIDES``) — the
    log line then agrees with the HTTP status actually sent rather than the
    domain default.
    """
    is_caller_error = report.error_domain == ErrorDomain.INPUT
    fields: dict[str, Any] = {
        "event": "api_error",
        "request_id": request_id,
        "route": request.url.path,
        **_request_correlation_fields(request),
        "error_type": report.error_type,
        "error_category": report.error_category,
        "error_domain": report.error_domain,
        "retryable": report.retryable,
        "status": status if status is not None else report.http_status,
        "provider": report.provider,
        "model": report.model,
    }
    metadata = report.provider_metadata
    if metadata is not None:
        fields["provider_status_code"] = metadata.status_code
        fields["provider_request_id"] = metadata.request_id
    emit_error_log(fields=fields, as_error=not is_caller_error)


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
        **_request_correlation_fields(request),
        "error_type": document.get("error_type"),
        "error_domain": error_domain,
        "retryable": document.get("retryable"),
        "status": status,
        "detail": document.get("detail"),
    }
    emit_error_log(fields=fields, as_error=not is_caller_error)


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


_ERROR_TYPE_STATUS_OVERRIDES: dict[str, int] = {
    # Async execution disabled is a deliberate deployment configuration, not
    # an unexpected runtime fault. Mapping it to 501 Not Implemented (rather
    # than the CONFIG-domain default of 500) tells clients "this server does
    # not provide this functionality" without inviting retries.
    "AsyncExecutionNotEnabledError": 501,
}


def _http_status_for(report: ErrorReport) -> int:
    """Return the HTTP status to use for this report.

    Defers to ``report.http_status`` for the standard mapping (provider-429
    passthrough + ``error_domain`` -> 4xx/5xx) and applies a per-error-type
    override last. Overrides live in ``_ERROR_TYPE_STATUS_OVERRIDES`` so the
    decisions are discoverable in one place — keep the dict small and each
    entry justified.
    """
    return _ERROR_TYPE_STATUS_OVERRIDES.get(report.error_type, report.http_status)


def _problem_response(report: ErrorReport, *, request: Request, disclosure_mode: DisclosureMode) -> JSONResponse:
    """Build the RFC 7807 `JSONResponse` for an `ErrorReport` and log the entry.

    Shared by the `PipelexError` and `TemporalError` handlers: both produce an
    `ErrorReport`, so both render and log it identically. The report is first
    made JSON-safe (see `_json_safe_report`); the status code then comes from
    `_http_status_for(report)` (the report's own mapping plus any API-layer
    override registered in ``_ERROR_TYPE_STATUS_OVERRIDES``), and the response
    body from the Phase 1 problem-document builder under the caller-supplied
    `disclosure_mode` (`register_exception_handlers` binds the app's effective
    mode into the handlers it registers).
    """
    report = _json_safe_report(report)
    request_id = _request_id_of(request)
    status = _http_status_for(report)
    document = build_problem_document(
        report,
        instance=request.url.path,
        request_id=request_id,
        disclosure_mode=disclosure_mode,
    )
    # Keep the RFC 7807 ``status`` member aligned with the actual HTTP status
    # when an API-layer override has bumped it away from ``report.http_status``;
    # otherwise the two surfaces (header vs body) would silently disagree.
    document["status"] = status
    _log_error_report(report, request=request, request_id=request_id, status=status)
    return JSONResponse(
        status_code=status,
        content=document,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        headers=_retry_after_header(report),
    )


async def handle_pipelex_error(request: Request, exc: Exception, *, disclosure_mode: DisclosureMode) -> Response:
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
    return _problem_response(report, request=request, disclosure_mode=disclosure_mode)


async def handle_temporal_error(request: Request, exc: Exception, *, disclosure_mode: DisclosureMode) -> Response:
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
    return _problem_response(report, request=request, disclosure_mode=disclosure_mode)


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
    emit_error_log(
        fields={
            "event": "api_error",
            "request_id": request_id,
            "route": request.url.path,
            **_request_correlation_fields(request),
            "error_type": type(exc).__name__,
            "error_category": "unknown",
            "error_domain": ErrorDomain.RUNTIME,
            "status": 500,
        },
        as_error=True,
    )
    # Route through the same builder every other API-authored 500 uses, so the
    # catch-all shape never drifts from `handle_api_error`'s output. The one
    # catch-all-specific addition is `error_category: "unknown"` — by definition
    # the failure was not classifiable, and the marker lets a downstream sink
    # tell a catch-all 500 apart from a classified `CONFIG`-domain one.
    document = build_problem_document_from_api_error(
        ErrorType.INTERNAL_SERVER_ERROR,
        "An unexpected error occurred. The request id is included for support.",
        500,
        instance=request.url.path,
        request_id=request_id,
        error_domain=ErrorDomain.RUNTIME,
    )
    document["error_category"] = "unknown"
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


def register_exception_handlers(app: FastAPI, *, disclosure_mode: DisclosureMode = DisclosureMode.VERBOSE) -> None:
    """Register the app-level exception handlers on `app`.

    Resolution is most-specific-first: an API-authored `ApiError` →
    `handle_api_error`; a FastAPI `RequestValidationError` (automatic
    request-body / parameter validation) → `handle_request_validation_error`; a
    `PipelexError` (including `WorkflowExecutionError`) → `handle_pipelex_error`;
    a non-pipelex `TemporalError` → `handle_temporal_error`; anything else →
    `handle_unexpected_error`. Registering `RequestValidationError` overrides
    FastAPI's built-in handler so its automatic-validation failures answer in
    the same `application/problem+json` shape as every other error, not the
    default `{"detail": [...]}`. Shared by the production app and by the unit
    tests, which register the same handlers on a throwaway app.

    `disclosure_mode` is captured by the two handlers that actually render a
    pipelex `ErrorReport` (`handle_pipelex_error`, `handle_temporal_error`) via
    the thin closures below — production passes the startup-resolved value
    (`api.main.ERROR_DISCLOSURE_MODE`); tests pass whatever the test needs and
    the default (`VERBOSE`) covers the common case. The other three handlers
    don't render an `ErrorReport`, so they don't need the mode and register
    directly.
    """

    async def _pipelex_error(request: Request, exc: Exception) -> Response:
        return await handle_pipelex_error(request, exc, disclosure_mode=disclosure_mode)

    async def _temporal_error(request: Request, exc: Exception) -> Response:
        return await handle_temporal_error(request, exc, disclosure_mode=disclosure_mode)

    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(PipelexError, _pipelex_error)
    app.add_exception_handler(TemporalError, _temporal_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
