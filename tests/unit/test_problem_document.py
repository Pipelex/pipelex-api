"""Unit tests for the RFC 7807 problem-document builder."""

import pytest
from pipelex.base_exceptions import DisclosureMode, ErrorReport, PipelexConfigError
from pipelex.cogt.inference.error_classification import ProviderErrorMetadata, UserAction, UserActionKind
from pipelex.cogt.inference.provider_name import ProviderName
from pipelex.system.environment import EnvVarNotFoundError
from pytest_mock import MockerFixture

from api.error_types import ErrorType
from api.problem_document import build_problem_document, build_problem_document_from_api_error


def _synthetic_llm_report() -> ErrorReport:
    """A fully-populated report standing in for a provider rate-limit failure."""
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


class TestProblemDocument:
    def test_builds_from_config_error(self):
        report = PipelexConfigError("the gateway config is missing").to_error_report()
        doc = build_problem_document(report, instance="/api/v1/validate", request_id="REQ2", disclosure_mode=DisclosureMode.VERBOSE)
        assert doc["status"] == 500
        assert doc["error_domain"] == "config"
        assert doc["error_type"] == "PipelexConfigError"
        assert doc["detail"] == "the gateway config is missing"
        assert doc["title"] == "Pipelex config"
        assert doc["type"].endswith("/pipelex-config-error/")
        assert doc["instance"] == "/api/v1/validate"
        assert doc["request_id"] == "REQ2"

    def test_builds_from_env_var_not_found_error(self):
        # The original bug: /pipeline/start with a required env var unset.
        report = EnvVarNotFoundError("Environment variable 'COMPLETION_CALLBACK_SECRET' is required but not set").to_error_report()
        doc = build_problem_document(report, instance="/api/v1/pipeline/start", request_id="REQ1", disclosure_mode=DisclosureMode.VERBOSE)
        assert doc["status"] == 500
        assert doc["type"] == "https://docs.pipelex.com/latest/errors/env-var-not-found-error/"
        assert doc["title"] == "Environment variable not set"
        assert "COMPLETION_CALLBACK_SECRET" in doc["detail"]
        assert doc["error_type"] == "EnvVarNotFoundError"
        assert doc["instance"] == "/api/v1/pipeline/start"
        assert doc["request_id"] == "REQ1"
        # Reconciliation: EnvVarNotFoundError is domain-less in this pipelex version
        # (a ToolError, no error_domain) — it still maps to HTTP 500, but the
        # error_domain extension member is absent rather than "config".
        assert "error_domain" not in doc

    def test_builds_from_report_with_provider_metadata(self):
        report = _synthetic_llm_report()
        doc = build_problem_document(report, instance="/api/v1/pipeline/execute", request_id="REQ7", disclosure_mode=DisclosureMode.VERBOSE)
        assert doc["status"] == 429  # provider-429 passthrough
        assert doc["type"] == "https://docs.pipelex.com/latest/errors/llm-completion-error/"
        assert doc["title"] == "LLM completion error"
        assert doc["detail"] == "OpenAI returned 429 (rate_limit_exceeded)"
        assert doc["error_type"] == "LLMCompletionError"
        assert doc["error_category"] == "transient"
        assert doc["error_domain"] == "runtime"
        assert doc["retryable"] is True
        assert doc["model"] == "gpt-4o-mini"
        assert doc["provider"] == "openai"
        assert doc["user_action"]["kind"] == "wait_and_retry"
        assert doc["provider_metadata"]["status_code"] == 429
        assert doc["provider_metadata"]["retry_after_seconds"] == 12.0
        # provider_metadata.body is excluded from serialization by pipelex.
        assert "body" not in doc["provider_metadata"]

    @pytest.mark.parametrize("disclosure_mode", [DisclosureMode.VERBOSE, DisclosureMode.STRICT])
    def test_forwards_disclosure_mode(self, mocker: MockerFixture, disclosure_mode: DisclosureMode):
        # The API only proves it forwards the mode unchanged; pipelex owns the
        # redaction rules and tests them upstream.
        report = PipelexConfigError("bad config").to_error_report()
        spy = mocker.patch.object(ErrorReport, "to_problem_document", wraps=report.to_problem_document)
        build_problem_document(report, instance="/x", request_id="r", disclosure_mode=disclosure_mode)
        assert spy.call_args.kwargs["disclosure_mode"] is disclosure_mode

    def test_api_error_variant_is_input_domain(self):
        doc = build_problem_document_from_api_error(
            ErrorType.VALIDATION_ERROR,
            "field 'pipe_code' is required",
            422,
            instance="/api/v1/pipeline/execute",
            request_id="REQ9",
        )
        assert doc["type"] == "https://docs.pipelex.com/latest/errors/validation-error/"
        assert doc["title"] == "Validation error"
        assert doc["status"] == 422
        assert doc["detail"] == "field 'pipe_code' is required"
        assert doc["instance"] == "/api/v1/pipeline/execute"
        assert doc["request_id"] == "REQ9"
        assert doc["error_type"] == "ValidationError"
        assert doc["error_domain"] == "input"

    def test_none_valued_fields_dropped(self):
        report = EnvVarNotFoundError("missing X").to_error_report()
        doc = build_problem_document(report, instance=None, request_id=None, disclosure_mode=DisclosureMode.VERBOSE)
        assert None not in doc.values()
        assert "instance" not in doc
        assert "request_id" not in doc
        assert "error_category" not in doc  # never set on this report

        api_doc = build_problem_document_from_api_error(ErrorType.BAD_REQUEST, "bad request", 400, instance=None, request_id=None)
        assert None not in api_doc.values()
        assert "instance" not in api_doc
        assert "request_id" not in api_doc
