"""`/validate` backend dispatch: Temporal-enabled goes through ONE worker round-trip, direct stays in-process.

When `temporal.is_enabled` is true, the route dispatches the whole job — validation sweep +
graph dry-run — as the one-step wrapper workflow (`wf_dry_validate` → `act_dry_validate`) via
`dispatch_dry_validate`, and shapes the same `ValidateResponse` envelope from the single
round-trip: the worker's `graph_spec` rides back directly, the blueprint is re-parsed API-side,
and `pipe_structures` come from a local load-only library acquisition. A validation failure
surfaces as `WorkflowExecutionError` carrying the recovered `ErrorReport` — the global
`PipelexError` handler renders the SAME RFC 7807 422 (`error_type=ValidateBundleError`,
`error_domain=input`) the direct path produces. Direct mode (`is_enabled=false`, the default in
tests) must never touch the dispatch helper.
"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.config import get_config
from pipelex.graph.graphspec import GraphSpec
from pipelex.pipeline.bundle_validator import DryRunOutput, DryRunStatus
from pipelex.pipeline.exceptions import ValidateBundleError
from pipelex.temporal.exceptions import WorkflowExecutionError
from pipelex.temporal.tprl_pipe.act_dry_validate import DryValidateArg, DryValidateResult
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from tests.unit._constants import SIGNATURE_MTHDS, VALID_MTHDS


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    register_exception_handlers(app)
    return TestClient(app)


def _stub_graph_spec() -> GraphSpec:
    return GraphSpec(graph_id="dry_run_graph_stub", created_at=datetime(2026, 1, 1, tzinfo=UTC))


def _stub_result(graph_spec: GraphSpec | None) -> DryValidateResult:
    return DryValidateResult(
        dry_run_outputs={
            "smoke.echo": DryRunOutput(pipe_code="echo", pipe_ref="smoke.echo", status=DryRunStatus.SUCCESS),
        },
        graph_spec=graph_spec,
    )


class TestValidateTemporalDispatch:
    def _enable_temporal(self, mocker: MockerFixture) -> None:
        mocker.patch.object(get_config().temporal, "is_enabled", True)

    def test_temporal_enabled_returns_envelope_from_one_round_trip(self, mocker: MockerFixture) -> None:
        self._enable_temporal(mocker)
        graph_spec = _stub_graph_spec()
        dispatch_mock = mocker.patch(
            "api.routes.pipelex.validate.dispatch_dry_validate",
            new=mocker.AsyncMock(return_value=_stub_result(graph_spec)),
        )

        response = _build_client().post("/api/v1/validate", json={"mthds_contents": [VALID_MTHDS]})

        assert response.status_code == 200, response.text
        body = response.json()
        # The worker's GraphSpec rides back on the envelope untouched.
        assert body["graph_spec"]["graph_id"] == "dry_run_graph_stub"
        # Blueprint re-parsed API-side; pipe_structures from the local load-only acquisition.
        assert body["pipelex_bundle_blueprint"]["main_pipe"] == "echo"
        assert "echo" in body["pipe_structures"]
        assert body["pipe_structures"]["echo"]["output"]["concept_code"].endswith("Text")
        assert body["success"] is True
        # Exactly one dispatch, carrying the request's contents + allow_signatures default.
        dispatch_mock.assert_awaited_once()
        assert dispatch_mock.await_args is not None
        dispatched_arg = dispatch_mock.await_args.args[0]
        assert isinstance(dispatched_arg, DryValidateArg)
        assert dispatched_arg.mthds_contents == [VALID_MTHDS]
        assert dispatched_arg.allow_signatures is False

    def test_temporal_enabled_best_effort_graph_none(self, mocker: MockerFixture) -> None:
        """A worker-side best-effort graph failure (graph_spec=None) still returns 200 with no graph."""
        self._enable_temporal(mocker)
        mocker.patch(
            "api.routes.pipelex.validate.dispatch_dry_validate",
            new=mocker.AsyncMock(return_value=_stub_result(None)),
        )

        response = _build_client().post("/api/v1/validate", json={"mthds_contents": [VALID_MTHDS]})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["graph_spec"] is None
        assert body["success"] is True
        assert "echo" in body["pipe_structures"]

    def test_temporal_enabled_validation_failure_renders_same_422(self, mocker: MockerFixture) -> None:
        """The recovered ErrorReport renders the SAME problem document as the direct path's 422."""
        self._enable_temporal(mocker)
        # The report a real worker failure carries back: the activity raised the categorized
        # ValidateBundleError, the boundary packed its report, the submitter recovered it.
        original_report = ValidateBundleError(
            message="Pipe 'sig_api.caller_seq' depends on PipeSignature placeholders that have no implementation: 'sig_api.summary_sig'"
        ).to_error_report()
        mocker.patch(
            "api.routes.pipelex.validate.dispatch_dry_validate",
            new=mocker.AsyncMock(side_effect=WorkflowExecutionError(original_report.message, error_report=original_report)),
        )

        response = _build_client().post("/api/v1/validate", json={"mthds_contents": [SIGNATURE_MTHDS]})

        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["error_type"] == "ValidateBundleError"
        assert body["error_domain"] == "input"
        # The structured offending refs survive in the caller-facing message.
        assert "sig_api.summary_sig" in body["detail"]

    def test_direct_mode_never_dispatches(self, mocker: MockerFixture) -> None:
        """With Temporal disabled (the default), the route never touches the dispatch helper."""
        dispatch_mock = mocker.patch(
            "api.routes.pipelex.validate.dispatch_dry_validate",
            new=mocker.AsyncMock(side_effect=AssertionError("direct mode must not dispatch to Temporal")),
        )

        response = _build_client().post("/api/v1/validate", json={"mthds_contents": [VALID_MTHDS]})

        assert response.status_code == 200, response.text
        dispatch_mock.assert_not_awaited()

    @pytest.mark.parametrize("allow_signatures", [True])
    def test_temporal_enabled_threads_allow_signatures(self, mocker: MockerFixture, allow_signatures: bool) -> None:
        self._enable_temporal(mocker)
        dispatch_mock = mocker.patch(
            "api.routes.pipelex.validate.dispatch_dry_validate",
            new=mocker.AsyncMock(
                return_value=DryValidateResult(
                    dry_run_outputs={
                        "sig_api.caller_seq": DryRunOutput(pipe_code="caller_seq", pipe_ref="sig_api.caller_seq", status=DryRunStatus.SUCCESS),
                    },
                    graph_spec=None,
                )
            ),
        )

        response = _build_client().post("/api/v1/validate", json={"mthds_contents": [SIGNATURE_MTHDS], "allow_signatures": allow_signatures})

        assert response.status_code == 200, response.text
        assert dispatch_mock.await_args is not None
        dispatched_arg = dispatch_mock.await_args.args[0]
        assert dispatched_arg.allow_signatures is True
