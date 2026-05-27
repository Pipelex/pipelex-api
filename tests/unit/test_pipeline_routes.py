"""Smoke + hardening tests for /pipeline/execute and /pipeline/start.

The actual pipeline runner is mocked: we only assert that the API layer
parses, validates, dispatches, and shapes responses correctly.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mthds.client.pipeline import PipelineState
from pipelex.pipeline.pipeline_response import PipelexPipelineStartResponse
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from api.routes.pipelex.pipeline import router as pipeline_router

VALID_MTHDS = (
    'domain = "smoke"\n'
    'main_pipe = "echo"\n'
    "\n"
    "[pipe.echo]\n"
    'type = "PipeLLM"\n'
    'description = "Echo the input"\n'
    'inputs = { text = "Text" }\n'
    'output = "Text"\n'
    'prompt = "Echo: @text"\n'
)


def _build_client(mocker: MockerFixture, *, with_request_id_middleware: bool = False) -> tuple[TestClient, Any, Any]:
    """Wire a FastAPI app whose pipeline runner is fully mocked.

    Returns (client, execute_mock, start_mock). `with_request_id_middleware`
    wraps the ASGI app in `RequestIdMiddleware` so an inbound `X-Request-ID`
    header binds the request-scoped contextvar the route reads.
    """
    app = FastAPI()
    app.include_router(pipeline_router, prefix="/api/v1")
    register_exception_handlers(app)

    fake_execute_response = mocker.MagicMock()
    fake_execute_response.model_dump.return_value = {
        "pipeline_run_id": "test-run-1",
        "created_at": "2026-01-15T12:00:00Z",
        "pipeline_state": "COMPLETED",
        "finished_at": "2026-01-15T12:00:01Z",
        "main_stuff_name": "main_stuff",
        "pipe_output": {"working_memory": {"root": {}, "aliases": {}}},
    }

    fake_start_response = PipelexPipelineStartResponse(
        pipeline_run_id="test-run-1",
        created_at="2026-01-15T12:00:00Z",
        pipeline_state=PipelineState.STARTED,
        workflow_id="wf-1",
    )

    fake_runner = mocker.MagicMock()
    fake_runner.execute_pipeline = mocker.AsyncMock(return_value=fake_execute_response)
    fake_runner.start_pipeline = mocker.AsyncMock(return_value=fake_start_response)
    mocker.patch("api.routes.pipelex.pipeline.ApiRunner", return_value=fake_runner)

    asgi_app = RequestIdMiddleware(app) if with_request_id_middleware else app
    return TestClient(asgi_app), fake_runner.execute_pipeline, fake_runner.start_pipeline


class TestPipelineRoutes:
    def test_execute_happy_path(self, mocker: MockerFixture):
        client, execute_mock, _ = _build_client(mocker)
        response = client.post(
            "/api/v1/pipeline/execute",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
        )
        assert response.status_code == 200
        execute_mock.assert_awaited_once()

    def test_execute_rejects_non_object_body(self, mocker: MockerFixture):
        client, _, _ = _build_client(mocker)
        response = client.post(
            "/api/v1/pipeline/execute",
            content=b'"just a string"',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidJSON"

    def test_execute_rejects_invalid_json(self, mocker: MockerFixture):
        client, _, _ = _build_client(mocker)
        response = client.post(
            "/api/v1/pipeline/execute",
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
            "/api/v1/pipeline/execute",
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
            "/api/v1/pipeline/execute",
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

    def test_start_happy_path(self, mocker: MockerFixture):
        client, _, start_mock = _build_client(mocker)
        response = client.post(
            "/api/v1/pipeline/start",
            json={
                "pipe_code": "echo",
                "mthds_contents": [VALID_MTHDS],
                "inputs": {"text": "hello"},
                "callback_urls": ["https://example.com/done"],
            },
        )
        assert response.status_code == 200
        start_mock.assert_awaited_once()
        kwargs = start_mock.await_args.kwargs
        assert kwargs["callback_urls"] == ["https://example.com/done"]

    def test_start_propagates_request_id_to_runner(self, mocker: MockerFixture):
        # The middleware binds the inbound `X-Request-ID` onto the request-scoped
        # contextvar; the route reads it via `get_request_id()` and passes it as
        # `request_id=` to `ApiRunner.start_pipeline`, which forwards it to
        # `pipeline_run_setup(...)` so it lands on `JobMetadata.request_id`.
        # Without this hop the worker's `WorkflowLog` would carry `None`.
        client, _, start_mock = _build_client(mocker, with_request_id_middleware=True)
        inbound_request_id = "01HNJZ4XR7K3Q9D8MWAQ7FY2E5"
        response = client.post(
            "/api/v1/pipeline/start",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
            headers={REQUEST_ID_HEADER: inbound_request_id},
        )
        assert response.status_code == 200
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
            "/api/v1/pipeline/start",
            json={"pipe_code": "echo", "callback_urls": [bad_url]},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidCallbackUrls"
        start_mock.assert_not_awaited()

    def test_start_rejects_too_many_callbacks(self, mocker: MockerFixture):
        client, _, start_mock = _build_client(mocker)
        response = client.post(
            "/api/v1/pipeline/start",
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
