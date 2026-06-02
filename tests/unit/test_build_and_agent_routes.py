"""Smoke + validation tests for /validate, /build/* and /build/{concept,pipe-spec,models}."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.hub import get_library_manager
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router

VALID_MTHDS = (
    'domain = "smoke"\n'
    'main_pipe = "echo"\n'
    "\n"
    "[pipe.echo]\n"
    'type = "PipeLLM"\n'
    'description = "Echo"\n'
    'inputs = { text = "Text" }\n'
    'output = "Text"\n'
    'prompt = "@text"\n'
)


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    register_exception_handlers(app)
    return TestClient(app)


class TestBuildAndAgentRoutes:
    def test_validate_rejects_oversized_mthds(self):
        client = _build_client()
        oversized = "a" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
        response = client.post(
            "/api/v1/validate",
            json={"mthds_contents": [oversized]},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    def test_validate_rejects_too_many_files(self):
        client = _build_client()
        response = client.post(
            "/api/v1/validate",
            json={"mthds_contents": [VALID_MTHDS] * 32},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    def test_validate_invalid_mthds_returns_rfc7807(self):
        # Regression for TODOS.md Q11: a `ValidateBundleError` (PipelexError
        # subclass, error_domain=INPUT) must propagate to the global handler
        # and render as RFC 7807 — NOT the legacy
        # `{success: false, mthds_contents, message}` envelope that this
        # endpoint used to return for 422 before Q11. Invalid TOML reliably
        # triggers a `ValidateBundleError` from the underlying interpreter.
        client = _build_client()
        response = client.post(
            "/api/v1/validate",
            json={"mthds_contents": ["this is not valid TOML !!!"]},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["error_type"] == "ValidateBundleError"
        assert body["error_domain"] == "input"
        assert body["status"] == 422
        # The pipelex message is preserved (caller-facing under
        # `_authors_caller_facing_message`) so the client gets the actual
        # interpreter complaint, not a generic placeholder.
        assert "TOML" in body["detail"]
        # Legacy fields must NOT appear in the failure envelope anymore.
        assert "success" not in body
        assert "mthds_contents" not in body
        assert "message" not in body

    def test_validate_missing_main_pipe_returns_rfc7807(self):
        # Regression for TODOS.md Q11: a bundle that parses cleanly but does
        # not declare a `main_pipe` is an API-side semantic precondition —
        # raised via `raise_validation_error` so it lands as RFC 7807 422
        # with `error_type = "ValidationError"`, not the legacy 400 envelope.
        bundle_without_main_pipe = (
            'domain = "smoke"\n\n[pipe.echo]\ntype = "PipeLLM"\ndescription = "Echo"\ninputs = { text = "Text" }\noutput = "Text"\nprompt = "@text"\n'
        )
        client = _build_client()
        response = client.post(
            "/api/v1/validate",
            json={"mthds_contents": [bundle_without_main_pipe]},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["error_type"] == "ValidationError"
        assert body["error_domain"] == "input"
        # 422 (not the legacy 400): the body is syntactically valid; the
        # bundle fails this endpoint's content rule.
        assert body["status"] == 422
        assert "main_pipe" in body["detail"]

    def test_build_inputs_rejects_oversized_pipe_code(self):
        client = _build_client()
        long_code = "x" * 1024
        response = client.post(
            "/api/v1/build/inputs",
            json={"mthds_contents": [VALID_MTHDS], "pipe_code": long_code},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    def test_build_concept_rejects_oversized_spec(self):
        client = _build_client()
        big_spec = {"description": "x" * (512 * 1024)}  # 512 KiB > 256 KiB cap
        response = client.post("/api/v1/build/concept", json={"spec": big_spec})
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    def test_build_pipe_spec_rejects_unknown_pipe_type(self):
        # `parse_pipe_spec` raises a bare `ValueError` for an unknown pipe_type
        # (documented in its docstring). The route must classify that as a
        # caller-input 422 — not let it escape as an opaque 500 through the
        # global `Exception` fallback. Regression for `TODOS.md` Q4.
        client = _build_client()
        response = client.post(
            "/api/v1/build/pipe-spec",
            json={"pipe_type": "NotARealPipeType", "spec": {"pipe_code": "x", "description": "d", "output": "Text"}},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["error_type"] == "ValidationError"
        assert "NotARealPipeType" in body["detail"]

    def test_models_rejects_invalid_category(self, mocker: MockerFixture):
        client = _build_client()
        # Patch list_models so we don't depend on real Pipelex setup if the
        # validation passes — but here we expect 422 before list_models is called.
        mocker.patch("api.routes.pipelex.agent.models.list_models")
        response = client.get("/api/v1/models?type=not-a-real-category")
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidModelCategory"

    @pytest.mark.parametrize(
        ("path", "payload"),
        [
            ("/api/v1/validate", {"mthds_contents": []}),
            ("/api/v1/build/inputs", {"mthds_contents": [], "pipe_code": "x"}),
            ("/api/v1/build/output", {"mthds_contents": [], "pipe_code": "x"}),
            ("/api/v1/build/runner", {"mthds_contents": [], "pipe_code": "x"}),
        ],
    )
    def test_empty_mthds_contents_rejected(self, path: str, payload: dict[str, object]):
        client = _build_client()
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    def test_build_runner_tears_down_library_when_set_current_library_raises(self, mocker: MockerFixture):
        # Regression for TODOS.md Q6: a failure between open_library() succeeding
        # and the try/finally entering must NOT leak the library. Before the
        # fix, open_library + set_current_library lived OUTSIDE the try, so a
        # set_current_library exception (theoretically: KeyboardInterrupt,
        # MemoryError, asyncio cancellation on the contextvar set) would skip
        # the teardown. After the fix, both calls live inside the try, with
        # library_id initialized to None so a pre-open failure is a no-op for
        # teardown rather than a leak.
        library_manager = get_library_manager()
        open_spy = mocker.spy(library_manager, "open_library")
        teardown_spy = mocker.spy(library_manager, "teardown")
        mocker.patch(
            "api.routes.pipelex.build.runner.set_current_library",
            side_effect=RuntimeError("synthetic set_current_library failure"),
        )

        # raise_server_exceptions=False: the synthetic RuntimeError reaches the
        # catch-all `Exception` handler, but Starlette's ServerErrorMiddleware
        # would otherwise re-raise it through the TestClient in test mode. The
        # convention matches `test_exception_handlers.py`.
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        register_exception_handlers(app)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/build/runner",
            json={"mthds_contents": [VALID_MTHDS], "pipe_code": "echo"},
        )

        # The synthetic RuntimeError propagates to the global Exception handler.
        assert response.status_code == 500
        # open_library DID run and create a library — capture its id.
        assert open_spy.spy_return is not None
        created_library_id, _ = open_spy.spy_return
        # The fix: teardown runs anyway, with the exact id open_library returned.
        teardown_spy.assert_called_once_with(library_id=created_library_id)

    def test_build_runner_succeeds_and_returns_python_code(self):
        # Phase 3b: /build/runner now validates via BundleValidator.validate_pipes (the public inner
        # sweep) against the library it just opened. The inner sweep never tears the library down, so
        # it stays loaded + current for generate_runner_code. A 200 with non-empty python_code proves
        # both halves: the dry-run sweep passed AND the library survived for code generation.
        client = _build_client()
        response = client.post(
            "/api/v1/build/runner",
            json={"mthds_contents": [VALID_MTHDS], "pipe_code": "echo"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["pipe_code"] == "echo"
        assert body["python_code"]
        assert "echo" in body["python_code"]

    def test_build_runner_keeps_library_open_for_codegen_then_tears_down_once(self, mocker: MockerFixture):
        # The loaded-on-success contract (D6): the inner sweep must NOT tear the library down — if it
        # did, get_required_pipe + generate_runner_code would have failed before the response. Teardown
        # happens exactly once, in the route's finally, after code generation.
        library_manager = get_library_manager()
        teardown_spy = mocker.spy(library_manager, "teardown")
        client = _build_client()
        response = client.post(
            "/api/v1/build/runner",
            json={"mthds_contents": [VALID_MTHDS], "pipe_code": "echo"},
        )
        assert response.status_code == 200, response.text
        teardown_spy.assert_called_once()
