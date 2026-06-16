"""`/validate` structured error envelope + per-file `source` threading (Issue 5 / Phase 2).

On an invalid bundle the route answers 422 RFC 7807 `application/problem+json` carrying a
structured `validation_errors[]` list — the per-error diagnostics the VS Code extension maps to
per-line problems. When the caller sends `mthds_sources` parallel to `mthds_contents`, each name is
threaded onto `blueprint.source`, so pipe/concept and blueprint errors name the owning file on
BOTH the failure path (`validation_errors[].source`) and the success path
(`bundle_blueprint.source`). A caller-supplied `mthds_sources`/`mthds_contents` length mismatch is a
request-shape error (422) caught before the runtime — which would otherwise treat the mismatch as
an internal 500.

Asserts the REAL wire response: the conformance suite is CLI-only and never spawns the HTTP server
(decision D5), so this is the executable verification the spec's Error-contract `<!-- unverified -->`
marker points at.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.base_exceptions import ValidationErrorCategory
from pipelex.pipeline.bundle_validator import DryRunOutput, DryRunStatus
from pipelex.pipeline.pipe_io_contracts import IOMultiplicity, PipeInputContract, PipeIOContract, PipeOutputContract
from pipelex.temporal.tprl_pipe.act_dry_validate import DryValidateResult
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from tests.unit._constants import INVALID_MAIN_PIPE_MTHDS, NO_MAIN_PIPE_MTHDS, VALID_MTHDS


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/v1")
    register_exception_handlers(app)
    return TestClient(app)


class TestValidateErrors:
    def test_invalid_bundle_returns_422_with_structured_validation_errors(self):
        client = _build_client()
        response = client.post("/v1/validate", json={"mthds_contents": [INVALID_MAIN_PIPE_MTHDS]})

        assert response.status_code == 422, response.text
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        # RFC 7807 slots + the pipelex classification extension the spec documents.
        assert body["error_type"] == "ValidateBundleError"
        # The structured per-error list the extension maps to per-line diagnostics — the field
        # that regressed (dropped at the exception->ErrorReport boundary) before Phase 1.
        items: list[dict[str, Any]] = body["validation_errors"]
        assert isinstance(items, list)
        assert items, body
        assert all("category" in item and "message" in item for item in items)

    def test_validation_errors_carry_threaded_source(self):
        client = _build_client()
        response = client.post(
            "/v1/validate",
            json={"mthds_contents": [INVALID_MAIN_PIPE_MTHDS], "mthds_sources": ["broken.mthds"]},
        )

        assert response.status_code == 422, response.text
        items: list[dict[str, Any]] = response.json()["validation_errors"]
        # Pins that the dict-seeded blueprint-validation item carries the threaded name (not some
        # coincidental other item): a regression that stopped seeding `source` would fail here.
        sourced = [item for item in items if item.get("source") == "broken.mthds"]
        assert sourced, f"no validation_errors item carried the threaded source: {items}"
        assert any(item["category"] == ValidationErrorCategory.BLUEPRINT_VALIDATION for item in sourced)

    def test_valid_bundle_threads_source_onto_blueprint(self):
        client = _build_client()
        response = client.post(
            "/v1/validate",
            json={"mthds_contents": [VALID_MTHDS], "mthds_sources": ["api://smoke.mthds"]},
        )

        assert response.status_code == 200, response.text
        # Success path threads the same name, so a client can correlate the report's primary
        # blueprint to its file exactly as it would a failure's `validation_errors[].source`.
        assert response.json()["bundle_blueprint"]["source"] == "api://smoke.mthds"

    def test_length_mismatch_is_a_request_error(self):
        client = _build_client()
        response = client.post(
            "/v1/validate",
            json={"mthds_contents": [VALID_MTHDS], "mthds_sources": ["a.mthds", "b.mthds"]},
        )

        # Caught at request validation as a 422 — never reaches the runtime (which would 500 on
        # the mismatch). The validator message rides the problem `detail`.
        assert response.status_code == 422, response.text
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "mthds_sources" in response.text

    def test_temporal_backend_threads_mthds_sources_through_dispatch(self, mocker: MockerFixture):
        # Issue 5 on the Temporal arm: the per-content names ride the dispatched DryValidateArg
        # AND the API-side blueprint parse threads them onto `bundle_blueprint.source`.
        worker_result = DryValidateResult(
            dry_run_outputs={"nomain.echo": DryRunOutput(pipe_code="echo", pipe_ref="nomain.echo", status=DryRunStatus.SUCCESS)},
            graph_spec=None,
            pending_signatures=[],
            pipe_io_contracts={
                "nomain.echo": PipeIOContract(
                    inputs={"text": PipeInputContract(concept_ref="native.Text", json_schema={"type": "string"})},
                    output=PipeOutputContract(concept_ref="native.Text", multiplicity=IOMultiplicity.SINGLE),
                )
            },
        )
        fake_config = mocker.MagicMock()
        fake_config.temporal.is_enabled = True
        mocker.patch("api.routes.pipelex.pipeline.get_config", return_value=fake_config)
        dispatch_mock: Any = mocker.patch(
            "api.routes.pipelex.pipeline.dispatch_dry_validate",
            new=mocker.AsyncMock(return_value=worker_result),
        )

        client = _build_client()
        response = client.post(
            "/v1/validate",
            json={"mthds_contents": [NO_MAIN_PIPE_MTHDS], "mthds_sources": ["api://nomain.mthds"]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["bundle_blueprint"]["source"] == "api://nomain.mthds"
        dispatch_mock.assert_awaited_once()
        dispatched_arg = dispatch_mock.await_args.args[0]
        assert dispatched_arg.mthds_sources == ["api://nomain.mthds"]
