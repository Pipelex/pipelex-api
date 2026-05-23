"""Integration tests for the global FastAPI exception handlers.

The production routes still catch their own exceptions (Phase 3 removes that),
so these tests register the same three handlers on a throwaway app whose routes
raise straight through — exercising exactly the path Phase 3 will leave behind.
"""

import re
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pipelex.base_exceptions import (
    INTERNAL_ERROR_PLACEHOLDER,
    DisclosureMode,
    ErrorDomain,
    ErrorReport,
    PipelexConfigError,
    PipelexError,
)
from pipelex.cogt.inference.error_classification import ProviderErrorMetadata, UserAction, UserActionKind
from pipelex.cogt.inference.provider_name import ProviderName
from pipelex.system.environment import EnvVarNotFoundError
from pipelex.temporal.exceptions import WorkflowExecutionError
from pydantic import BaseModel, ConfigDict
from pytest_mock import MockerFixture
from temporalio.exceptions import TemporalError
from typing_extensions import override

from api.error_types import ErrorType
from api.errors import raise_internal_server_error, raise_validation_error
from api.main import PROBLEM_JSON_MEDIA_TYPE, register_exception_handlers
from api.middleware import REQUEST_ID_HEADER, RequestIdMiddleware

# Crockford Base32, 26 chars — the ULID alphabet RequestIdMiddleware mints.
_ULID_RE = re.compile(r"\A[0-9A-HJKMNP-TV-Z]{26}\Z")


class _SimulatedLLMError(PipelexError):
    """A `PipelexError` whose report mimics a provider rate-limit (429) failure."""

    @override
    def to_error_report(self) -> ErrorReport:
        return ErrorReport(
            error_type="LLMCompletionError",
            message="OpenAI returned 429 (rate_limit_exceeded)",
            title="LLM completion error",
            type_uri="https://docs.pipelex.com/latest/errors/llm-completion-error/",
            error_category="transient",
            error_domain="runtime",
            retryable=True,
            user_action=UserAction(kind=UserActionKind.WAIT_AND_RETRY, detail="Retry after 12s."),
            model="gpt-4o-mini",
            provider="openai",
            provider_metadata=ProviderErrorMetadata(
                provider=ProviderName.OPENAI,
                sdk_exception_type="RateLimitError",
                status_code=429,
                retry_after_seconds=12.0,
                request_id="req_provider_abc",
            ),
        )


class _CorruptReportError(PipelexError):
    """A `PipelexError` whose `to_error_report` itself fails — the handler-of-handlers case."""

    @override
    def to_error_report(self) -> ErrorReport:
        msg = "to_error_report is deliberately broken"
        raise RuntimeError(msg)


class _CallerFacingInputError(PipelexError):
    """An `INPUT`-domain error whose message is genuine caller-facing copy.

    `_authors_caller_facing_message` makes STRICT disclosure keep the message
    verbatim — the projection pipelex applies to a `.mthds` syntax error.
    """

    error_domain = ErrorDomain.INPUT
    _authors_caller_facing_message = True


class _FakeTemporalTransportError(TemporalError):
    """A non-`PipelexError` `TemporalError` subclass — a Temporal transport failure."""


def _report_with_retry(*, status_code: int, retry_after_seconds: float | None) -> ErrorReport:
    """Build a provider-error report carrying a `retry_after_seconds` hint."""
    return ErrorReport(
        error_type="LLMCompletionError",
        message="provider error",
        title="LLM completion error",
        type_uri="https://docs.pipelex.com/latest/errors/llm-completion-error/",
        error_category="transient",
        error_domain="runtime",
        retryable=True,
        provider_metadata=ProviderErrorMetadata(
            provider=ProviderName.OPENAI,
            sdk_exception_type="RateLimitError",
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
        ),
    )


# Reports exercising every branch of `_retry_after_header`: a finite hint, the
# two non-finite values a provider could emit, a negative value, and a hint
# carried on a non-429 status (where `Retry-After` must not be emitted).
_RETRY_REPORTS: dict[str, ErrorReport] = {
    "finite": _report_with_retry(status_code=429, retry_after_seconds=12.4),
    "infinite": _report_with_retry(status_code=429, retry_after_seconds=float("inf")),
    "nan": _report_with_retry(status_code=429, retry_after_seconds=float("nan")),
    "negative": _report_with_retry(status_code=429, retry_after_seconds=-5.0),
    "non_429": _report_with_retry(status_code=503, retry_after_seconds=30.0),
}


class _CraftedRetryError(PipelexError):
    """A `PipelexError` whose report is selected by key — exercises `_retry_after_header`."""

    def __init__(self, report_key: str) -> None:
        super().__init__("crafted retry-after scenario")
        self._report = _RETRY_REPORTS[report_key]

    @override
    def to_error_report(self) -> ErrorReport:
        return self._report


# Route handlers are deliberately not underscore-prefixed: a private,
# decorator-only function reads as unused to the type checker.
_router = APIRouter()


@_router.get("/config-error")
async def config_error_route() -> None:
    msg = "the gateway config is missing"
    raise PipelexConfigError(msg)


@_router.get("/env-error")
async def env_error_route() -> None:
    msg = "Environment variable 'COMPLETION_CALLBACK_SECRET' is required but not set"
    raise EnvVarNotFoundError(msg)


@_router.get("/input-error")
async def input_error_route() -> None:
    msg = "your .mthds file has a syntax error on line 4"
    raise _CallerFacingInputError(msg)


@_router.get("/llm-error")
async def llm_error_route() -> None:
    msg = "simulated provider rate limit"
    raise _SimulatedLLMError(msg)


@_router.get("/corrupt-error")
async def corrupt_error_route() -> None:
    msg = "its report builder is broken"
    raise _CorruptReportError(msg)


@_router.get("/workflow-error")
async def workflow_error_route() -> None:
    msg = "the workflow failed"
    raise WorkflowExecutionError(msg)


@_router.get("/temporal-transport-error")
async def temporal_transport_error_route() -> None:
    msg = "temporal cluster unreachable"
    raise _FakeTemporalTransportError(msg)


@_router.get("/unexpected-error")
async def unexpected_error_route() -> None:
    msg = "something nobody anticipated"
    raise RuntimeError(msg)


@_router.get("/crafted-retry/{report_key}")
async def crafted_retry_route(report_key: str) -> None:
    raise _CraftedRetryError(report_key)


@_router.get("/api-input-error")
async def api_input_error_route() -> None:
    # An API-authored 422 — the INPUT-domain branch of `handle_api_error`.
    raise_validation_error("a caller-side mistake")


@_router.get("/api-config-error")
async def api_config_error_route() -> None:
    # An API-authored 500 — the CONFIG-domain branch of `handle_api_error`
    # (the `version.py` `PackageNotFoundError → raise_internal_server_error`
    # path goes through this same code).
    raise_internal_server_error("the configuration is broken", error_type=ErrorType.SERVER_MISCONFIGURED)


class _RequestValidationBody(BaseModel):
    """Trivial schema for `test_request_validation_error_emits_structured_warning_log`.

    A POST that is both missing `field` and carries an unknown key triggers
    FastAPI's automatic `RequestValidationError`, which is what we want to
    log-test. `extra="forbid"` matches the strictest production schemas
    (`UploadRequest`, `ResolveStorageUrlRequest`) and ensures both the
    missing-field and the extra-field branches of the validation surface
    fire on a single request — Pydantic's default `extra="ignore"` would
    silently drop the unknown key.
    """

    model_config = ConfigDict(extra="forbid")

    field: str


@_router.post("/needs-body")
async def needs_body_route(_body: _RequestValidationBody) -> None:
    return None


def _build_client(*, raise_server_exceptions: bool = True) -> TestClient:
    """Wire a throwaway app with the production handlers and request-id middleware.

    `raise_server_exceptions` must be `False` for tests that hit the catch-all
    handler: Starlette's `ServerErrorMiddleware` always re-raises after running
    its handler, so the `TestClient` would otherwise surface the exception
    instead of the sanitized response.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(_router)
    return TestClient(RequestIdMiddleware(app), raise_server_exceptions=raise_server_exceptions)


class TestExceptionHandlers:
    def test_pipelex_error_produces_problem_json(self):
        response = _build_client().get("/config-error")
        assert response.status_code == 500
        assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
        body = response.json()
        assert body["type"].endswith("/pipelex-config-error/")
        assert body["title"] == "Pipelex config"
        assert body["status"] == 500
        assert body["detail"] == "the gateway config is missing"
        assert body["error_type"] == "PipelexConfigError"
        assert body["error_domain"] == "config"
        assert body["instance"] == "/config-error"
        assert body["request_id"] == response.headers[REQUEST_ID_HEADER]

    def test_env_var_not_found_error_is_domainless_500(self):
        # Reconciliation #1: EnvVarNotFoundError is a domain-less ToolError in
        # this pipelex version — HTTP 500, but no `error_domain` member. The
        # plan's original "error_domain = config" expectation is wrong here;
        # the test above covers a genuine config-domain error.
        response = _build_client().get("/env-error")
        assert response.status_code == 500
        body = response.json()
        assert "COMPLETION_CALLBACK_SECRET" in body["detail"]
        assert body["error_type"] == "EnvVarNotFoundError"
        assert "error_domain" not in body

    def test_strict_disclosure_redacts_config_preserves_input(self, mocker: MockerFixture):
        # The handler forwards the startup-resolved disclosure mode unchanged;
        # pipelex owns the redaction. STRICT redacts a non-caller-facing message
        # and keeps a caller-facing one.
        mocker.patch("api.main.ERROR_DISCLOSURE_MODE", DisclosureMode.STRICT)
        client = _build_client()

        config_response = client.get("/config-error")
        assert config_response.status_code == 500
        assert config_response.json()["detail"] == INTERNAL_ERROR_PLACEHOLDER
        assert "the gateway config is missing" not in config_response.text

        input_response = client.get("/input-error")
        assert input_response.status_code == 422
        assert input_response.json()["detail"] == "your .mthds file has a syntax error on line 4"

    def test_provider_rate_limit_emits_retry_after(self):
        response = _build_client().get("/llm-error")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "12"
        body = response.json()
        assert body["error_type"] == "LLMCompletionError"
        assert body["retryable"] is True
        assert body["provider_metadata"]["status_code"] == 429

    @pytest.mark.parametrize(
        ("report_key", "expected_status", "expected_retry_after"),
        [
            ("finite", 429, "13"),  # 12.4 rounds up
            ("infinite", 429, None),  # non-finite is dropped — no crash, no header
            ("nan", 429, None),  # non-finite is dropped — no crash, no header
            ("negative", 429, "0"),  # clamped to a non-negative integer
            ("non_429", 500, None),  # the hint never rides a non-429 response
        ],
    )
    def test_retry_after_header_is_guarded(self, report_key: str, expected_status: int, expected_retry_after: str | None):
        response = _build_client().get(f"/crafted-retry/{report_key}")
        assert response.status_code == expected_status
        if expected_retry_after is None:
            assert "retry-after" not in response.headers
        else:
            assert response.headers["Retry-After"] == expected_retry_after

    def test_unexpected_error_falls_back_to_sanitized_500(self):
        response = _build_client(raise_server_exceptions=False).get("/unexpected-error")
        assert response.status_code == 500
        assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
        body = response.json()
        assert body["error_type"] == "InternalServerError"
        assert body["error_domain"] == "runtime"
        assert body["retryable"] is False
        assert body["type"].endswith("/internal-server-error/")
        assert body["instance"] == "/unexpected-error"
        assert body["error_category"] == "unknown"
        # The real exception class and message never reach the client.
        assert "RuntimeError" not in response.text
        assert "something nobody anticipated" not in response.text

    def test_temporal_error_dispatch(self):
        client = _build_client()

        # WorkflowExecutionError IS a PipelexError — routed to the PipelexError
        # handler, so its error_type is the class name, not the transport label.
        workflow_response = client.get("/workflow-error")
        assert workflow_response.status_code == 500
        assert workflow_response.json()["error_type"] == "WorkflowExecutionError"

        # A bare temporalio TemporalError — routed to the dedicated handler,
        # which authors the transport-transient classification.
        transport_response = client.get("/temporal-transport-error")
        assert transport_response.status_code == 500
        transport_body = transport_response.json()
        assert transport_body["error_type"] == "TemporalTransportError"
        assert transport_body["error_category"] == "transient"
        assert transport_body["retryable"] is True

    def test_handler_of_handlers_catches_corrupt_report(self):
        # When to_error_report() itself raises, the failure escapes the
        # PipelexError handler; ServerErrorMiddleware funnels it into the
        # catch-all — a sanitized 500, never a bodyless default.
        response = _build_client(raise_server_exceptions=False).get("/corrupt-error")
        assert response.status_code == 500
        body = response.json()
        assert body["error_type"] == "InternalServerError"
        assert "_CorruptReportError" not in response.text
        assert "to_error_report is deliberately broken" not in response.text

    @pytest.mark.parametrize(
        ("method", "path", "json_body", "expected_status"),
        [
            ("GET", "/config-error", None, 500),
            ("GET", "/env-error", None, 500),
            ("GET", "/input-error", None, 422),
            ("GET", "/llm-error", None, 429),
            ("GET", "/workflow-error", None, 500),
            ("GET", "/temporal-transport-error", None, 500),
            ("GET", "/corrupt-error", None, 500),
            ("GET", "/unexpected-error", None, 500),
            # Cover the API-authored paths (`handle_api_error`) and FastAPI's
            # automatic-validation path (`handle_request_validation_error`):
            # the request-id propagation is the same structural property, but
            # the three branches build the response document differently and
            # are worth sweeping.
            ("GET", "/api-input-error", None, 422),
            ("GET", "/api-config-error", None, 500),
            ("POST", "/needs-body", {"wrong": 1}, 422),
        ],
    )
    def test_request_id_present_on_every_error_response(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None,
        expected_status: int,
    ):
        response = _build_client(raise_server_exceptions=False).request(method, path, json=json_body)
        assert response.status_code == expected_status
        request_id = response.headers[REQUEST_ID_HEADER]
        assert _ULID_RE.match(request_id) is not None
        assert response.json()["request_id"] == request_id

    def test_inbound_request_id_is_echoed(self):
        response = _build_client().get("/config-error", headers={REQUEST_ID_HEADER: "inbound-correlation-007"})
        assert response.headers[REQUEST_ID_HEADER] == "inbound-correlation-007"
        assert response.json()["request_id"] == "inbound-correlation-007"

    def test_api_authored_500_emits_structured_error_log(self, mocker: MockerFixture):
        # Without `handle_api_error` logging, an `ApiError`-shaped 500 produces
        # zero operator output (`/pipelex_version`'s `PackageNotFoundError →
        # raise_internal_server_error` is the canonical silent case). Assert
        # the handler emits one `event=api_error` line at `error` level with
        # the response fields — same shape `_log_error_report` emits for a
        # pipelex-derived 500, so a downstream log sink sees them uniformly.
        log_spy = mocker.patch("api.main.log")
        response = _build_client().get("/api-config-error")
        assert response.status_code == 500
        log_spy.error.assert_called_once()
        rendered = log_spy.error.call_args.args[0]
        assert "event=api_error" in rendered
        assert "status=500" in rendered
        assert "error_type=ServerMisconfigured" in rendered
        assert "error_domain=config" in rendered
        assert "retryable=False" in rendered
        assert "route=/api-config-error" in rendered
        assert "detail=the configuration is broken" in rendered
        assert log_spy.error.call_args.kwargs == {"include_exception": True}
        log_spy.warning.assert_not_called()

    def test_api_authored_4xx_emits_structured_warning_log(self, mocker: MockerFixture):
        # Mirror at the warning level: an INPUT-domain `ApiError` is a caller
        # mistake, not an operator fault, so it logs at `warning` without a
        # traceback — same disposition rule `_log_error_report` uses.
        log_spy = mocker.patch("api.main.log")
        response = _build_client().get("/api-input-error")
        assert response.status_code == 422
        log_spy.warning.assert_called_once()
        rendered = log_spy.warning.call_args.args[0]
        assert "event=api_error" in rendered
        assert "status=422" in rendered
        assert "error_type=ValidationError" in rendered
        assert "error_domain=input" in rendered
        assert "retryable=False" in rendered
        assert "detail=a caller-side mistake" in rendered
        log_spy.error.assert_not_called()

    def test_request_validation_error_emits_structured_warning_log(self, mocker: MockerFixture):
        # FastAPI's automatic-validation 422 goes through
        # `handle_request_validation_error`, not `handle_api_error`, but emits
        # the same `event=api_error` warning line so the surface is uniform —
        # whichever code path rejects a caller-input failure, the operator
        # log shape is identical.
        log_spy = mocker.patch("api.main.log")
        # `{"wrong": 1}` triggers FastAPI's automatic validation failure on
        # `_RequestValidationBody`: `field` is missing AND `wrong` is an
        # extra key (the body uses `extra="forbid"`, so the extra-key path
        # actually fires — Pydantic's default `extra="ignore"` would have
        # left only the missing-field error).
        response = _build_client().post("/needs-body", json={"wrong": 1})
        assert response.status_code == 422
        log_spy.warning.assert_called_once()
        rendered = log_spy.warning.call_args.args[0]
        assert "event=api_error" in rendered
        assert "status=422" in rendered
        assert "error_type=ValidationError" in rendered
        assert "error_domain=input" in rendered
        # The summary covers both per-field failures the request triggered.
        assert "field" in rendered
        assert "wrong" in rendered
        log_spy.error.assert_not_called()
