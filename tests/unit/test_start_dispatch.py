"""`/start` derives the fire-and-forget variant of the deployment's execution_mode.

Fire-and-forget vs blocking is a property of the ENDPOINT, not of the deployment: `/execute` and
`/validate` dispatch `execution_mode` synchronously as-is, while `/start` is asynchronous and
dispatches the fire-and-forget sibling of the configured backend (`_async_start_mode`). A
`temporal_blocking` deployment therefore dispatches `temporal_fire_and_forget` on `/start`;
`direct`/`mistral_native` have no fire-and-forget variant and dispatch unchanged, blocking until
completion — `direct` answers 202 with `workflow_id=None`, while `mistral_native` answers with the
non-null `workflow_id` its orchestrator returns (the run id). This pins the derivation both as a
pure mapping (`_async_start_mode` over every mode) and end-to-end on `POST /start`, with a stub
orchestrator registered under the derived mode (the boot slot is never used).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.pipe_run.delivery_assignment import DeliveryAssignment
from pipelex.pipe_run.pipe_job import PipeJob
from pipelex.plugins.orchestrator_registry import OrchestratorRegistry
from pipelex.runtime_bridge.execution_mode import PipelexExecutionMode
from pipelex.runtime_bridge.payloads import PipelexPipeRunOutput
from pytest_mock import MockerFixture

from api.api_config import ApiConfig
from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from api.routes.pipelex.pipeline import (
    _async_start_mode,  # pyright: ignore[reportPrivateUsage]  # route-local helper, tested directly as a pure mapping
)
from tests.unit._constants import VALID_MTHDS

_PIPELINE_NS = "api.routes.pipelex.pipeline"


class TestAsyncStartModeMapping:
    """`_async_start_mode` as a pure mapping over every execution mode.

    Only Temporal has a fire-and-forget sibling, so `temporal_blocking` derives
    `temporal_fire_and_forget` (and an already-f&f value is idempotent); `direct` and
    `mistral_native` have none and map to themselves. The `match` is exhaustive over the enum, so a
    new member added without an arm here is a pyright failure — this test additionally pins the
    intended mapping against an accidental edit (e.g. `mistral_native` silently mapped to a Temporal
    arm).
    """

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            (PipelexExecutionMode.DIRECT, PipelexExecutionMode.DIRECT),
            (PipelexExecutionMode.TEMPORAL_BLOCKING, PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET),
            (PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET, PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET),
            (PipelexExecutionMode.MISTRAL_NATIVE, PipelexExecutionMode.MISTRAL_NATIVE),
        ],
    )
    def test_async_start_mode_maps_to_fire_and_forget_sibling(self, configured: PipelexExecutionMode, expected: PipelexExecutionMode) -> None:
        assert _async_start_mode(configured) == expected


class _RecordingStub:
    """Stand-in orchestrator that records the call and returns the configured `workflow_id`.

    Models each backend's `/start` answer: the Temporal f&f arm returns a `workflow_id` immediately;
    `direct` runs in-process and returns `None`; `mistral_native` runs per-call (awaiting completion)
    and returns its run id as the `workflow_id`. The value is echoed straight through.
    """

    def __init__(self, *, workflow_id: str | None) -> None:
        self.calls: list[DeliveryAssignment | None] = []
        self._workflow_id = workflow_id

    async def run(self, *, pipe_job: PipeJob, delivery_assignment: DeliveryAssignment | None) -> PipelexPipeRunOutput:
        self.calls.append(delivery_assignment)
        return PipelexPipeRunOutput(
            output_dict={},
            main_stuff_name=None,
            pipeline_run_id=pipe_job.job_metadata.pipeline_run_id,
            workflow_id=self._workflow_id,
            is_completed=False,
            graph_spec_dump=None,
        )


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/v1")
    register_exception_handlers(app)
    return TestClient(app)


def _force_config(mocker: MockerFixture, *, mode: PipelexExecutionMode) -> None:
    config = ApiConfig(execution_mode=mode, allow_request_execution_mode_override=False)
    mocker.patch(f"{_PIPELINE_NS}.get_api_config", return_value=config)


def _register_stub(mocker: MockerFixture, *, mode: PipelexExecutionMode, stub: _RecordingStub) -> None:
    mocker.patch(f"{_PIPELINE_NS}.get_orchestrator_registry", return_value=OrchestratorRegistry({mode: stub}))


class TestStartDerivesAsyncVariant:
    """End-to-end: `POST /start` dispatches the orchestrator registered under the DERIVED mode.

    Each case configures a synchronous deployment mode and registers the stub ONLY under the mode
    `_async_start_mode` derives — had `/start` dispatched the configured mode raw, the registry
    (holding no arm there) would have raised `MissingOrchestratorError`. The echoed `workflow_id`
    proves which backend answered: a `temporal_blocking` deployment dispatches the f&f arm (non-null
    id), `direct` answers with `null`, and `mistral_native` dispatches its per-call arm and answers
    with the non-null run id.
    """

    @pytest.mark.parametrize(
        ("configured", "dispatched", "stub_workflow_id", "expected_workflow_id"),
        [
            (
                PipelexExecutionMode.TEMPORAL_BLOCKING,
                PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET,
                "wf-123",
                "wf-123",
            ),
            (PipelexExecutionMode.DIRECT, PipelexExecutionMode.DIRECT, None, None),
            (
                PipelexExecutionMode.MISTRAL_NATIVE,
                PipelexExecutionMode.MISTRAL_NATIVE,
                "run-abc",
                "run-abc",
            ),
        ],
    )
    def test_start_dispatches_derived_mode(
        self,
        mocker: MockerFixture,
        configured: PipelexExecutionMode,
        dispatched: PipelexExecutionMode,
        stub_workflow_id: str | None,
        expected_workflow_id: str | None,
    ) -> None:
        _force_config(mocker, mode=configured)
        stub = _RecordingStub(workflow_id=stub_workflow_id)
        _register_stub(mocker, mode=dispatched, stub=stub)

        response = _build_client().post(
            "/v1/start",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["state"] == "STARTED"
        assert body["workflow_id"] == expected_workflow_id
        assert len(stub.calls) == 1
