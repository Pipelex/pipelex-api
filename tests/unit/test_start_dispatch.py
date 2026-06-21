"""`/start` derives the fire-and-forget variant of the deployment's execution_mode.

Fire-and-forget vs blocking is a property of the ENDPOINT, not of the deployment: `/execute` and
`/validate` dispatch `execution_mode` synchronously as-is, while `/start` is asynchronous and
dispatches the fire-and-forget sibling of the configured backend (`_async_start_mode`). A
`temporal_blocking` deployment therefore dispatches `temporal_fire_and_forget` on `/start`;
`direct`/`mistral_native` have no async variant and run in-process answering 202 with
`workflow_id=None`. This pins the derivation both as a pure mapping and end-to-end on `POST /start`
with a stub orchestrator registered under the derived mode (the boot slot is never used).
"""

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
from tests.unit._constants import VALID_MTHDS

_PIPELINE_NS = "api.routes.pipelex.pipeline"


class _RecordingStub:
    """Stand-in orchestrator that records the call and returns a fire-and-forget-style output.

    `workflow_id` is echoed straight through (the f&f arm returns it immediately); a `None`
    `workflow_id` models the direct/mistral case where `/start` runs in-process and answers with no
    workflow id.
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
    def test_temporal_blocking_deployment_dispatches_fire_and_forget(self, mocker: MockerFixture) -> None:
        """A `temporal_blocking` deployment's `/start` dispatches the FIRE-AND-FORGET orchestrator.

        The stub is registered ONLY under `temporal_fire_and_forget`; the deployment default is
        `temporal_blocking`. A 202 with the stub called (and the workflow_id echoed) proves `/start`
        looked up the DERIVED async mode — had it dispatched `temporal_blocking` raw, the registry
        (which holds no blocking arm) would have raised `MissingOrchestratorError`.
        """
        _force_config(mocker, mode=PipelexExecutionMode.TEMPORAL_BLOCKING)
        stub = _RecordingStub(workflow_id="wf-123")
        _register_stub(mocker, mode=PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET, stub=stub)

        response = _build_client().post(
            "/v1/start",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["state"] == "STARTED"
        assert body["workflow_id"] == "wf-123"
        assert len(stub.calls) == 1

    def test_direct_deployment_dispatches_direct_with_no_workflow_id(self, mocker: MockerFixture) -> None:
        """The base (`direct`) has no async variant: `/start` dispatches `direct` and answers with workflow_id null."""
        _force_config(mocker, mode=PipelexExecutionMode.DIRECT)
        stub = _RecordingStub(workflow_id=None)
        _register_stub(mocker, mode=PipelexExecutionMode.DIRECT, stub=stub)

        response = _build_client().post(
            "/v1/start",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["state"] == "STARTED"
        assert body["workflow_id"] is None
        assert len(stub.calls) == 1
