"""Smoke + hardening tests for /execute and /start (MTHDS Protocol run routes).

The actual pipeline runner is mocked: we only assert that the API layer
parses, validates, dispatches, and shapes responses correctly.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.base_exceptions import PipelexConfigError
from pipelex.cogt.llm.llm_report import LLMTokensUsage
from pipelex.cogt.usage.cost_category import CostCategory
from pipelex.cogt.usage.token_category import TokenCategory
from pipelex.pipeline.job_metadata import JobMetadata
from pipelex.pipeline.pipeline_response import PipelexRunResultStart, RunState
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from api.routes.pipelex.pipeline import router as pipeline_router
from tests.unit._constants import VALID_MTHDS


def _build_client(mocker: MockerFixture, *, with_request_id_middleware: bool = False) -> tuple[TestClient, Any, Any]:
    """Wire a FastAPI app whose pipeline runner is fully mocked.

    Returns (client, execute_mock, start_mock). `with_request_id_middleware`
    wraps the ASGI app in `RequestIdMiddleware` so an inbound `X-Request-ID`
    header binds the request-scoped contextvar the route reads.
    """
    app = FastAPI()
    app.include_router(pipeline_router, prefix="/v1")
    register_exception_handlers(app)

    fake_execute_response = mocker.MagicMock()
    fake_execute_response.model_dump.return_value = {
        "pipeline_run_id": "test-run-1",
        "created_at": "2026-01-15T12:00:00Z",
        "state": "COMPLETED",
        "finished_at": "2026-01-15T12:00:01Z",
        "main_stuff_name": "main_stuff",
        "pipe_output": {"working_memory": {"root": {}, "aliases": {}}},
    }

    fake_start_response = PipelexRunResultStart(
        pipeline_run_id="test-run-1",
        created_at="2026-01-15T12:00:00Z",
        state=RunState.STARTED,
        workflow_id="wf-1",
    )

    fake_runner = mocker.MagicMock()
    fake_runner.execute = mocker.AsyncMock(return_value=fake_execute_response)
    fake_runner.start = mocker.AsyncMock(return_value=fake_start_response)
    mocker.patch("api.routes.pipelex.pipeline.ApiRunner", return_value=fake_runner)

    asgi_app = RequestIdMiddleware(app) if with_request_id_middleware else app
    return TestClient(asgi_app), fake_runner.execute, fake_runner.start


class TestPipelineRoutes:
    def test_execute_happy_path(self, mocker: MockerFixture):
        client, execute_mock, _ = _build_client(mocker)
        response = client.post(
            "/v1/execute",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
        )
        assert response.status_code == 200
        execute_mock.assert_awaited_once()

    def test_execute_trims_tokens_usages_to_wire_records(self, mocker: MockerFixture):
        """/execute emits TokensUsageRecord wire records on pipe_output.tokens_usages — never
        the internal usage models with their job_metadata plumbing and unit_costs rate table.
        """
        client, execute_mock, _ = _build_client(mocker)
        tokens_usage = LLMTokensUsage(
            job_metadata=JobMetadata(user_id="user-1", pipeline_run_id="plr-1", pipe_code="echo"),
            inference_model_name="test-model",
            inference_model_id="test-model-id",
            nb_tokens_by_category={TokenCategory.INPUT: 10, TokenCategory.OUTPUT: 5},
            unit_costs={CostCategory.INPUT: 1.0, CostCategory.OUTPUT: 2.0},
        )
        fake_response = execute_mock.return_value
        fake_response.pipe_output.tokens_usages = [tokens_usage]
        fake_response.model_dump.return_value["pipe_output"]["tokens_usages"] = [tokens_usage.model_dump(mode="json")]

        response = client.post(
            "/v1/execute",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
        )
        assert response.status_code == 200
        records = response.json()["pipe_output"]["tokens_usages"]
        assert len(records) == 1
        record = records[0]
        assert record["model_type"] == "llm"
        assert record["pipe_code"] == "echo"
        assert record["nb_tokens_by_category"] == {"input": 10, "output": 5}
        assert record["cost"] == 10 * (1.0 / 1_000_000) + 5 * (2.0 / 1_000_000)
        assert "job_metadata" not in record
        assert "unit_costs" not in record

    def test_execute_rejects_non_object_body(self, mocker: MockerFixture):
        client, _, _ = _build_client(mocker)
        response = client.post(
            "/v1/execute",
            content=b'"just a string"',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidJSON"

    def test_execute_rejects_invalid_json(self, mocker: MockerFixture):
        client, _, _ = _build_client(mocker)
        response = client.post(
            "/v1/execute",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidJSON"

    def test_execute_rejects_recursion_error(self, mocker: MockerFixture):
        # A `RecursionError` raised inside `kajson.loads` (e.g. from a deeply-
        # nested JSON array exhausting the interpreter's recursion budget) is a
        # caller-input failure and must map to 422 InvalidJSON, not escape to the
        # catch-all 500 handler. Mocked rather than crafted because whether
        # `json.JSONDecoder` recurses on a given input depends on the C accelerator
        # availability — the mock pins the post-catch contract regardless.
        client, _, _ = _build_client(mocker)
        mocker.patch(
            "api.routes.pipelex.pipeline.kajson.loads",
            side_effect=RecursionError("maximum recursion depth exceeded"),
        )
        response = client.post(
            "/v1/execute",
            content=b'{"any": "valid-json-here"}',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        problem = response.json()
        assert problem["error_type"] == "InvalidJSON"
        assert problem["error_domain"] == "input"
        # The opaque-500 sentinel must never appear for a caller-input failure.
        assert problem["error_type"] != "InternalServerError"

    @pytest.mark.parametrize(
        ("label", "body"),
        [
            # KajsonDecoderError — bad module name.
            ("bad_module", b'{"__class__": "X", "__module__": "no_such_module_xyz"}'),
            # KajsonDecoderError — class not found in an importable module.
            ("class_not_in_module", b'{"__class__": "NoSuchClass", "__module__": "json"}'),
            # KajsonDecoderError — enum value mismatch.
            (
                "enum_bad_value",
                b'{"__class__": "ErrorType", "__module__": "api.error_types", "_value_": "not_a_real_value"}',
            ),
            # Unwrapped KeyError — `__class__` present without `__module__`.
            ("missing_module_marker", b'{"__class__": "X"}'),
            # Unwrapped KeyError — same leak nested inside an outer object.
            ("nested_missing_module_marker", b'{"outer": {"__class__": "X"}}'),
            # Unwrapped AttributeError — generic-typed class whose base also resolves to nothing.
            ("generic_base_missing", b'{"__class__": "Foo[Bar]", "__module__": "json"}'),
            # Unwrapped TypeError — `__class__` is not a string.
            ("class_not_a_string", b'{"__class__": 42, "__module__": "json"}'),
        ],
    )
    def test_execute_rejects_crafted_kajson_payloads(self, mocker: MockerFixture, label: str, body: bytes):
        """Every documented kajson decode failure — and the bare `KeyError` /
        `AttributeError` / `TypeError` that escape kajson's `__class__` /
        `__module__` handling on crafted markers — is a caller mistake and
        must map to a 422 RFC 7807 problem document, never an opaque 500.
        """
        client, _, _ = _build_client(mocker)
        response = client.post(
            "/v1/execute",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422, label
        assert response.headers["content-type"] == "application/problem+json", label
        problem = response.json()
        assert problem["error_type"] == "InvalidJSON", label
        assert problem["error_domain"] == "input", label
        # The opaque-500 sentinel must never appear for a crafted body.
        assert problem["error_type"] != "InternalServerError", label

    def test_start_happy_path_returns_202(self, mocker: MockerFixture):
        client, _, start_mock = _build_client(mocker)
        response = client.post(
            "/v1/start",
            json={
                "pipe_code": "echo",
                "mthds_contents": [VALID_MTHDS],
                "inputs": {"text": "hello"},
                "callback_urls": ["https://example.com/done"],
            },
        )
        # Protocol: `POST /start` answers 202 Accepted with a StartAck.
        assert response.status_code == 202
        body = response.json()
        assert body["pipeline_run_id"] == "test-run-1"
        assert body["state"] == "STARTED"
        start_mock.assert_awaited_once()
        kwargs = start_mock.await_args.kwargs
        assert kwargs["callback_urls"] == ["https://example.com/done"]

    def test_start_forwards_client_pipeline_run_id(self, mocker: MockerFixture):
        # D11: the open-source runner ACCEPTS a client-supplied pipeline_run_id and
        # forwards it to the runner's `start` as the `pipeline_run_id` kwarg.
        client, _, start_mock = _build_client(mocker)
        response = client.post(
            "/v1/start",
            json={
                "pipe_code": "echo",
                "mthds_contents": [VALID_MTHDS],
                "inputs": {"text": "hello"},
                "pipeline_run_id": "client-chosen-run-42",
            },
        )
        assert response.status_code == 202
        start_mock.assert_awaited_once()
        assert start_mock.await_args.kwargs["pipeline_run_id"] == "client-chosen-run-42"

    def test_parse_request_binds_pipe_code_and_pipeline_run_id_to_state(self, mocker: MockerFixture):
        # End-to-end: a real POST that goes through `_parse_request` must bind
        # `pipe_code` / `pipeline_run_id` on `request.state` so that a
        # downstream failure (here: `start` raising `PipelexConfigError`)
        # is logged with both fields. The unit-level tests pin the
        # handler->getter->log path; this one pins that `_parse_request` itself
        # actually writes to `request.state` against the production route.
        # Both values are kept free of logfmt-active characters (whitespace,
        # `=`, `"`) so the substring assertions below match the unquoted
        # rendering. Future test additions that exercise quoted values should
        # parse the logfmt line via the `_parse_logfmt` helper in
        # `test_exception_handlers.py` instead of substring matching.
        client, _, start_mock = _build_client(mocker)
        body_pipe_code = "echo"
        body_pipeline_run_id = "run-end-to-end-0001"
        start_mock.side_effect = PipelexConfigError("simulated config fault inside the runner")
        log_spy = mocker.patch("api.exception_handlers.log")
        response = client.post(
            "/v1/start",
            json={
                "pipe_code": body_pipe_code,
                "mthds_contents": [VALID_MTHDS],
                "inputs": {"text": "hello"},
                "pipeline_run_id": body_pipeline_run_id,
            },
        )
        assert response.status_code == 500
        log_spy.error.assert_called_once()
        rendered = log_spy.error.call_args.args[0]
        assert f"pipe_code={body_pipe_code}" in rendered
        assert f"pipeline_run_id={body_pipeline_run_id}" in rendered

    def test_parse_request_drops_empty_correlation_fields(self, mocker: MockerFixture):
        # An empty-string `pipe_code` / `pipeline_run_id` in the body must NOT
        # render as a bare `pipe_code=` token in the operator log — the bare
        # token reads as a logfmt parse error to downstream sinks and defeats
        # grep-by-value. `_coerce_correlation_field` normalizes empty strings
        # to `None`, and `emit_error_log` drops `None`-valued fields.
        client, _, start_mock = _build_client(mocker)
        start_mock.side_effect = PipelexConfigError("simulated config fault")
        log_spy = mocker.patch("api.exception_handlers.log")
        response = client.post(
            "/v1/start",
            json={
                "pipe_code": "",
                "mthds_contents": [VALID_MTHDS],
                "inputs": {"text": "hello"},
                "pipeline_run_id": "",
            },
        )
        assert response.status_code == 500
        log_spy.error.assert_called_once()
        rendered = log_spy.error.call_args.args[0]
        # No bare token of either kind — neither `pipe_code= ` nor at end-of-line.
        assert "pipe_code=" not in rendered
        assert "pipeline_run_id=" not in rendered

    def test_parse_request_caps_oversized_pipe_code(self, mocker: MockerFixture):
        # `RunRequest.pipe_code` carries no Pydantic max_length, so a
        # caller can in principle send a megabyte-long string. The binding
        # site caps the value rendered into operator logs so a single failed
        # request cannot blow per-line log-sink budgets. 256 is the limit;
        # anything longer is silently truncated for the log surface (the
        # actual `run_request.pipe_code` passed to the runner is
        # unchanged — only the `request.state` mirror is capped).
        client, _, start_mock = _build_client(mocker)
        start_mock.side_effect = PipelexConfigError("simulated config fault")
        log_spy = mocker.patch("api.exception_handlers.log")
        oversized = "x" * 5000
        response = client.post(
            "/v1/start",
            json={
                "pipe_code": oversized,
                "mthds_contents": [VALID_MTHDS],
                "inputs": {"text": "hello"},
            },
        )
        assert response.status_code == 500
        log_spy.error.assert_called_once()
        rendered = log_spy.error.call_args.args[0]
        # The capped value (256 x's) appears in the log; the original 5000-x
        # string does NOT — proves the cap fires and bounds the per-line cost.
        assert f"pipe_code={'x' * 256}" in rendered
        assert "x" * 5000 not in rendered

    def test_parse_request_binds_pipe_code_before_extras_validation(self, mocker: MockerFixture):
        # The binding must run BEFORE `_validate_extras` so an SSRF-rejected
        # callback URL (or any other extras-validation 422) still rides the
        # caller's `pipe_code` into the operator log. The unit-level tests
        # cannot exercise this ordering — only an end-to-end POST does.
        client, _, _ = _build_client(mocker)
        log_spy = mocker.patch("api.exception_handlers.log")
        body_pipe_code = "echo"
        response = client.post(
            "/v1/start",
            json={
                "pipe_code": body_pipe_code,
                # An AWS-metadata URL — blocked by `_is_disallowed_host`, so
                # `_validate_extras` raises 422 before `from_body` runs.
                "callback_urls": ["http://169.254.169.254/latest/meta-data/"],
            },
        )
        assert response.status_code == 422
        # An INPUT-domain 422 logs at `warning`, not `error`.
        log_spy.warning.assert_called_once()
        rendered = log_spy.warning.call_args.args[0]
        assert f"pipe_code={body_pipe_code}" in rendered

    def test_start_propagates_request_id_to_runner(self, mocker: MockerFixture):
        # The middleware binds the inbound `X-Request-ID` onto the request-scoped
        # contextvar; the route reads it via `get_request_id()` and passes it as
        # `request_id=` to `ApiRunner.start`, which forwards it to
        # `pipeline_run_setup(...)` so it lands on `JobMetadata.request_id`.
        # Without this hop the worker's `WorkflowLog` would carry `None`.
        client, _, start_mock = _build_client(mocker, with_request_id_middleware=True)
        inbound_request_id = "01HNJZ4XR7K3Q9D8MWAQ7FY2E5"
        response = client.post(
            "/v1/start",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
            headers={REQUEST_ID_HEADER: inbound_request_id},
        )
        assert response.status_code == 202
        assert response.headers[REQUEST_ID_HEADER] == inbound_request_id
        start_mock.assert_awaited_once()
        assert start_mock.await_args.kwargs["request_id"] == inbound_request_id

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://169.254.169.254/latest/meta-data/",  # AWS metadata
            "http://127.0.0.1:8081/internal",  # loopback
            "http://10.0.0.5/private",  # private RFC1918
            "http://localhost/x",  # localhost name
            "file:///etc/passwd",  # disallowed scheme
            "ftp://example.com/x",  # disallowed scheme
        ],
    )
    def test_start_rejects_ssrf_callbacks(self, mocker: MockerFixture, bad_url: str):
        client, _, start_mock = _build_client(mocker)
        response = client.post(
            "/v1/start",
            json={"pipe_code": "echo", "callback_urls": [bad_url]},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidCallbackUrls"
        start_mock.assert_not_awaited()

    def test_start_rejects_too_many_callbacks(self, mocker: MockerFixture):
        client, _, start_mock = _build_client(mocker)
        response = client.post(
            "/v1/start",
            json={
                "pipe_code": "echo",
                "callback_urls": [f"https://example.com/{idx}" for idx in range(20)],
            },
        )
        # `callback_urls` exceeds `PipelineApiExtras.max_length`. The route
        # validates extras explicitly (`_validate_extras`) and re-raises the
        # resulting Pydantic `ValidationError` via `raise_validation_error`
        # with the more-specific `InvalidCallbackUrls` error_type — so the
        # response is RFC 7807 422 / `application/problem+json` but classified
        # one level finer than the generic FastAPI automatic-validation path.
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidCallbackUrls"
        start_mock.assert_not_awaited()
