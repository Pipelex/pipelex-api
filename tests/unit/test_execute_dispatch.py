"""`/execute` dispatches by orchestration_mode through the OrchestratorRegistry (full synchronous output).

Pins the dispatch + output-mapping independent of any real backend, with a stub orchestrator: the
runner resolves the deployment's orchestration_mode, dispatches the locally-built PipeJob through the
orchestrator the registry holds for it with `DeliveryMode.BLOCKING`, and rehydrates the orchestrator's
JSON-safe output back into the full PipeOutput the `/execute` response wraps — exercising the real
serialize -> rehydrate round-trip (`serialize_completed_output` -> `hydrate_working_memory`), including
the `graph_spec` `strict=False` re-validation branch. Also pins the policy-gated per-request override
(symmetric with `/start`) and the no-orchestrator case (`MissingOrchestratorError`). The boot slot is
never used — every mode dispatches through the per-call registry. (Delivery is endpoint-set, never
requestable, so `/execute` has no fire-and-forget refusal — that axis is `/start`'s.)
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.core.memory.working_memory import MAIN_STUFF_NAME
from pipelex.core.pipes.pipe_output import PipeOutput
from pipelex.graph.graphspec import GraphSpec
from pipelex.pipe_run.delivery_assignment import DeliveryAssignment
from pipelex.pipe_run.pipe_job import PipeJob
from pipelex.plugins.orchestrator_registry import OrchestratorRegistry
from pipelex.runtime_bridge.exceptions import MissingOrchestratorError
from pipelex.runtime_bridge.payloads import PipelexPipeDispatchAck, PipelexPipeRunOutput
from pipelex.runtime_bridge.serialization import serialize_completed_output
from pytest_mock import MockerFixture

from api.api_config import ApiConfig
from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from api.routes.pipelex.pipeline import ApiRunner
from tests.unit._constants import VALID_MTHDS

_PIPELINE_NS = "api.routes.pipelex.pipeline"


class _StubOrchestrator:
    """A backend-agnostic stand-in orchestrator: echoes the job's working memory back as completed output.

    Returning via `serialize_completed_output` is the point — it produces the real JSON-safe
    `PipelexPipeRunOutput` (the same shape that crosses the Temporal worker boundary), so the route
    exercises the production serialize -> rehydrate round-trip instead of a hand-built payload. It
    records each dispatch so a test can assert `/execute` drove the blocking `execute` arm. `start` (the
    fire-and-forget arm) is present only to satisfy the protocol — `/execute` never calls it.
    """

    def __init__(self, *, graph_spec: GraphSpec | None = None, supports_fire_and_forget: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._graph_spec = graph_spec
        self.supports_fire_and_forget = supports_fire_and_forget

    async def execute(self, *, pipe_job: PipeJob, delivery_assignment: DeliveryAssignment | None) -> PipelexPipeRunOutput:
        self.calls.append({"pipe_code": pipe_job.pipe.code, "delivery_assignment": delivery_assignment})
        # A completed run always delivers a main stuff (pipelex invariant; enforced by
        # `resolve_main_stuff_root_key` in both `serialize_completed_output` and `from_pipe_output`).
        # This stub is an echo: promote the job's input stuff to the run's main stuff via the
        # `main_stuff` alias, so the serialize -> rehydrate round-trip yields a valid completed memory.
        working_memory = pipe_job.get_working_memory()
        working_memory.set_alias(alias=MAIN_STUFF_NAME, target=next(iter(working_memory.root)))
        return serialize_completed_output(
            pipe_output=PipeOutput(
                working_memory=working_memory,
                pipeline_run_id=pipe_job.job_metadata.pipeline_run_id,
                graph_spec=self._graph_spec,
            ),
            workflow_id=None,
        )

    async def start(self, *, pipe_job: PipeJob, delivery_assignment: DeliveryAssignment | None) -> PipelexPipeDispatchAck:
        msg = "/execute drives the blocking `execute` arm; `start` must not be reached."
        raise NotImplementedError(msg)


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/v1")
    register_exception_handlers(app)
    return TestClient(app)


def _register_stub(mocker: MockerFixture, *, mode: str, stub: _StubOrchestrator) -> None:
    """Patch the orchestrator registry so the route's mode lookup finds `stub` for `mode`."""
    registry = OrchestratorRegistry({mode: stub})
    mocker.patch(f"{_PIPELINE_NS}.get_orchestrator_registry", return_value=registry)


def _force_config(mocker: MockerFixture, *, mode: str, allow_override: bool) -> None:
    """Patch the api config so `resolve_orchestration_mode` sees `mode` as the deployment default + policy."""
    config = ApiConfig(orchestration_mode=mode, allow_request_orchestration_mode_override=allow_override)
    mocker.patch(f"{_PIPELINE_NS}.get_api_config", return_value=config)


class TestExecuteDispatch:
    def test_direct_dispatch_returns_rehydrated_full_output(self, mocker: MockerFixture) -> None:
        """`direct` (the packaged default) dispatches through the registry and returns the full output."""
        stub = _StubOrchestrator()
        _register_stub(mocker, mode="direct", stub=stub)

        client = _build_client()
        response = client.post(
            "/v1/execute",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "COMPLETED"
        # The full output survived the serialize -> rehydrate round-trip: the echo input is in the
        # rehydrated working memory the /execute response wraps.
        root = body["pipe_output"]["working_memory"]["root"]
        assert root["text"]["content"]["text"] == "hello"
        # The dispatch reached the registered orchestrator's blocking `execute` arm; /execute is
        # synchronous, so no delivery target (never the caller's to choose).
        assert len(stub.calls) == 1
        assert stub.calls[0]["delivery_assignment"] is None

    def test_graph_spec_survives_strict_false_rehydration(self, mocker: MockerFixture) -> None:
        """A non-None graph_spec round-trips through the helper's `strict=False` reverse of `model_dump(mode="json")`.

        Pins the most subtle line of `_pipe_output_from_run_output`: the orchestrator dumps `graph_spec`
        in JSON mode (so `GraphSpec.created_at`, a `strict=True` datetime, becomes an ISO string), and the
        helper must re-validate it with `strict=False` to restore the typed `GraphSpec`. Without the
        graph_spec branch exercised, a regression there (strict default, wrong key) would stay green.
        """
        graph_spec = GraphSpec(graph_id="g-1", created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
        stub = _StubOrchestrator(graph_spec=graph_spec)
        _register_stub(mocker, mode="direct", stub=stub)

        client = _build_client()
        response = client.post(
            "/v1/execute",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {"text": "hello"}},
        )

        assert response.status_code == 200, response.text
        # The graph_spec survived the dump -> strict=False re-validation: it is present in the response.
        assert response.json()["pipe_output"]["graph_spec"]["graph_id"] == "g-1"

    def test_per_request_override_honored_when_policy_allows(self, mocker: MockerFixture) -> None:
        """With override ON, a per-request orchestration_mode is resolved and dispatched (symmetric with /start)."""
        _force_config(mocker, mode="direct", allow_override=True)
        stub = _StubOrchestrator()
        _register_stub(mocker, mode="temporal", stub=stub)

        client = _build_client()
        response = client.post(
            "/v1/execute",
            json={
                "pipe_code": "echo",
                "mthds_contents": [VALID_MTHDS],
                "inputs": {"text": "hello"},
                "orchestration_mode": "temporal",
            },
        )

        assert response.status_code == 200, response.text
        # The requested (non-default) backend was honored: dispatch reached the temporal-keyed stub.
        assert len(stub.calls) == 1

    def test_forbidden_orchestration_mode_override_is_a_403(self) -> None:
        """The route threads orchestration_mode into the same override policy /start uses: a forbidden override is a 403."""
        client = _build_client()
        # Packaged default is `direct` with override OFF; forcing a different backend is refused before dispatch.
        response = client.post(
            "/v1/execute",
            json={
                "pipe_code": "echo",
                "mthds_contents": [VALID_MTHDS],
                "inputs": {"text": "hello"},
                "orchestration_mode": "temporal",
            },
        )

        assert response.status_code == 403, response.text
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["error_type"] == "OrchestrationModeOverrideForbidden"

    @pytest.mark.asyncio
    async def test_missing_orchestrator_for_resolved_mode_raises(self, mocker: MockerFixture) -> None:
        """A resolved mode with no registered orchestrator fails loud with MissingOrchestratorError."""
        mocker.patch(f"{_PIPELINE_NS}.get_orchestrator_registry", return_value=OrchestratorRegistry({}))

        with pytest.raises(MissingOrchestratorError) as exc_info:
            await ApiRunner().execute(
                pipe_code="echo",
                mthds_contents=[VALID_MTHDS],
                inputs={"text": "hello"},
            )
        # The packaged default is `direct`; the empty registry holds no orchestrator for it.
        assert exc_info.value.mode == "direct"
