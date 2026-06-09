"""Smoke + validation tests for /validate, /build/* and /build/{concept,pipe-spec,models}."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.hub import get_library_manager
from pipelex.pipe_run.exceptions import DryRunError
from pipelex.pipeline.bundle_validator import DryRunOutput, DryRunStatus
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from tests.unit._constants import VALID_MTHDS


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

    @pytest.mark.parametrize("path", ["/api/v1/build/inputs", "/api/v1/build/output"])
    def test_build_route_reuses_validate_bundle_library_without_leaking(self, path: str, mocker: MockerFixture):
        # C-2 / Q-5: /build/inputs and /build/output must NOT open a second library. validate_bundle
        # opens exactly one library and leaves it loaded + current on success; the route reads the pipe
        # from that library and tears down the same id. Before the fix the route opened a SECOND library
        # and tore down only that one, orphaning validate_bundle's library on every successful call.
        library_manager = get_library_manager()
        open_spy = mocker.spy(library_manager, "open_library")
        teardown_spy = mocker.spy(library_manager, "teardown")

        client = _build_client()
        response = client.post(path, json={"mthds_contents": [VALID_MTHDS], "pipe_code": "echo"})

        assert response.status_code == 200, response.text
        # Exactly one library opened (inside validate_bundle) — no second open in the route.
        assert open_spy.call_count == 1
        created_library_id, _ = open_spy.spy_return
        # ...and that exact library is the one torn down — no orphan left in LibraryManager._libraries.
        teardown_spy.assert_called_once_with(library_id=created_library_id)

    def test_build_runner_rejects_when_requested_pipe_is_skipped(self, mocker: MockerFixture):
        # C-3: a cross-package unresolved dependency makes validate_pipes record the requested pipe
        # SKIPPED (not a hard failure), so the sweep returns normally. generate_runner_code reads only
        # the pipe's own inputs/output, so without a guard the route would emit runner code for a
        # pipeline that cannot run. The guard must reject the requested-pipe-SKIPPED case with a 422
        # and never reach code generation. (Other, unrelated SKIPPED pipes stay tolerated.)
        skipped_result = {
            "smoke.echo": DryRunOutput(
                pipe_code="echo",
                pipe_ref="smoke.echo",
                status=DryRunStatus.SKIPPED,
                error_message="Skipped dry run for pipe 'smoke.echo': unresolved dependency: other_pkg.missing",
            )
        }
        mocker.patch(
            "api.routes.pipelex.build.runner.BundleValidator.validate_pipes",
            new=mocker.AsyncMock(return_value=skipped_result),
        )
        generate_spy = mocker.patch("api.routes.pipelex.build.runner.generate_runner_code")

        client = _build_client()
        response = client.post(
            "/api/v1/build/runner",
            json={"mthds_contents": [VALID_MTHDS], "pipe_code": "echo"},
        )

        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["error_type"] == "ValidationError"
        assert body["error_domain"] == "input"
        assert "echo" in body["detail"]
        # The guard short-circuits before code generation.
        generate_spy.assert_not_called()

    def test_build_runner_translates_dry_run_failure_to_422_not_500(self, mocker: MockerFixture):
        # /build/runner calls BundleValidator.validate_pipes directly (not through validate_bundle), so a
        # bare DryRunError — which carries no error_domain — would otherwise render as a 500 server fault.
        # A failed dry-run of a caller-submitted bundle is a caller-fixable INPUT error: the route
        # translates it to ValidateBundleError so it renders 422, matching what /validate, /build/inputs
        # and /build/output return for the identical failure (they go through validate_bundle's
        # _translate_to_validate_bundle_error). Without the translation this would be a 500.
        mocker.patch(
            "api.routes.pipelex.build.runner.BundleValidator.validate_pipes",
            new=mocker.AsyncMock(side_effect=DryRunError("Dry run failed with 1 unexpected pipe failure(s): 'smoke.echo'")),
        )
        generate_spy = mocker.patch("api.routes.pipelex.build.runner.generate_runner_code")

        client = _build_client()
        response = client.post(
            "/api/v1/build/runner",
            json={"mthds_contents": [VALID_MTHDS], "pipe_code": "echo"},
        )

        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        # Same wire shape the other three routes return for a dry-run failure: ValidateBundleError, INPUT.
        assert body["error_type"] == "ValidateBundleError"
        assert body["error_domain"] == "input"
        # The failure short-circuits before code generation.
        generate_spy.assert_not_called()

    def test_validate_tears_down_validate_bundle_library_without_leaking(self, mocker: MockerFixture):
        # validate_bundle opens a library and leaves it loaded + current on success (the D6 contract);
        # /validate must own that teardown. Before the fix the route never tore it down, orphaning it on
        # every successful call (the best-effort graph dry-run opens + tears down its OWN library via
        # PipelexRunner, so that one was balanced). Assert the conservation property: every library opened
        # during the request is torn down — open count == teardown count. A leak shows up as opens > teardowns.
        library_manager = get_library_manager()
        open_spy = mocker.spy(library_manager, "open_library")
        teardown_spy = mocker.spy(library_manager, "teardown")

        client = _build_client()
        response = client.post("/api/v1/validate", json={"mthds_contents": [VALID_MTHDS]})

        assert response.status_code == 200, response.text
        assert open_spy.call_count >= 1
        assert open_spy.call_count == teardown_spy.call_count
