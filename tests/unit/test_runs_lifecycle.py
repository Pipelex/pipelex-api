"""Tests for the runner-native async run lifecycle: start → poll → result by id.

The store transitions and the background executor are tested directly (deterministic,
no event-loop races); the result route's 202/200/409/404 mapping is tested via a
TestClient over a minimal app with a pre-seeded store.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mthds.client.pipeline import PipelineRequest
from pytest_mock import MockerFixture

from api.routes.pipelex import runs as runs_module
from api.routes.pipelex.runs import router as runs_router
from api.run_store import InMemoryRunStore, LeanRunStatus, RunRecord

_VALID_MTHDS = (
    'domain = "hello"\nmain_pipe = "echo"\n\n[pipe.echo]\ntype = "PipeLLM"\n'
    'description = "Echo"\ninputs = { text = "Text" }\noutput = "Text"\nprompt = "@text"\n'
)


def _build_client(store: InMemoryRunStore) -> TestClient:
    app = FastAPI()
    app.state.run_store = store
    app.state.run_tasks = set()
    app.include_router(runs_router, prefix="/api/v1")
    return TestClient(app)


def _seed(store: InMemoryRunStore, record: RunRecord) -> None:
    """Inject a record directly (white-box) so route tests need no event loop."""
    store._records[record.pipeline_run_id] = record  # pyright: ignore[reportPrivateUsage]


class TestRunsLifecycle:
    @pytest.mark.asyncio
    async def test_store_transitions_to_completed(self, mocker: MockerFixture):
        store = InMemoryRunStore()
        record = await store.create("run-1")
        assert record.status == "PENDING"

        await store.mark_running("run-1")
        running = await store.get("run-1")
        assert running is not None
        assert running.status == "RUNNING"

        fake_result = mocker.Mock()
        await store.mark_completed("run-1", fake_result)
        done = await store.get("run-1")
        assert done is not None
        assert done.status == "COMPLETED"
        assert done.result is fake_result
        assert done.finished_at is not None

    @pytest.mark.asyncio
    async def test_store_mark_failed(self):
        store = InMemoryRunStore()
        await store.create("run-2")
        await store.mark_failed("run-2", "boom")
        failed = await store.get("run-2")
        assert failed is not None
        assert failed.status == "FAILED"
        assert failed.error == "boom"
        assert failed.finished_at is not None

    @pytest.mark.asyncio
    async def test_get_unknown_returns_none(self):
        store = InMemoryRunStore()
        assert await store.get("nope") is None

    def test_is_terminal(self):
        assert LeanRunStatus.COMPLETED.is_terminal is True
        assert LeanRunStatus.FAILED.is_terminal is True
        assert LeanRunStatus.PENDING.is_terminal is False
        assert LeanRunStatus.RUNNING.is_terminal is False

    @pytest.mark.asyncio
    async def test_run_in_background_success(self, mocker: MockerFixture):
        store = InMemoryRunStore()
        await store.create("run-3")
        fake_response = mocker.Mock()
        runner_cls = mocker.patch.object(runs_module, "ApiRunner")
        runner_cls.return_value.execute_pipeline = mocker.AsyncMock(return_value=fake_response)

        request = PipelineRequest.from_body({"pipe_code": "echo", "mthds_contents": [_VALID_MTHDS], "inputs": {"text": "hi"}})
        await runs_module._run_in_background("run-3", request, "anonymous", store)  # pyright: ignore[reportPrivateUsage]

        done = await store.get("run-3")
        assert done is not None
        assert done.status == "COMPLETED"
        assert done.result is fake_response

    @pytest.mark.asyncio
    async def test_run_in_background_records_failure(self, mocker: MockerFixture):
        store = InMemoryRunStore()
        await store.create("run-4")
        runner_cls = mocker.patch.object(runs_module, "ApiRunner")
        runner_cls.return_value.execute_pipeline = mocker.AsyncMock(side_effect=ValueError("bad pipe"))

        request = PipelineRequest.from_body({"pipe_code": "echo", "mthds_contents": [_VALID_MTHDS], "inputs": {"text": "hi"}})
        await runs_module._run_in_background("run-4", request, "anonymous", store)  # pyright: ignore[reportPrivateUsage]

        failed = await store.get("run-4")
        assert failed is not None
        assert failed.status == "FAILED"
        assert "bad pipe" in (failed.error or "")

    def test_result_route_running_returns_202(self):
        store = InMemoryRunStore()
        _seed(store, RunRecord(pipeline_run_id="r-run", status=LeanRunStatus.RUNNING, created_at="2026-06-07T00:00:00Z"))
        client = _build_client(store)
        response = client.get("/api/v1/runs/by-id/r-run/result")
        assert response.status_code == 202
        assert response.headers["Retry-After"] == "2"

    def test_result_route_completed_returns_200(self, mocker: MockerFixture):
        store = InMemoryRunStore()
        record = RunRecord(pipeline_run_id="r-done", status=LeanRunStatus.COMPLETED, created_at="2026-06-07T00:00:00Z")
        mock_result = mocker.Mock()
        mock_result.model_dump.return_value = {"pipeline_run_id": "r-done", "pipeline_state": "COMPLETED"}
        record.result = mock_result
        _seed(store, record)
        client = _build_client(store)
        response = client.get("/api/v1/runs/by-id/r-done/result")
        assert response.status_code == 200
        assert response.json()["pipeline_state"] == "COMPLETED"

    def test_result_route_failed_returns_409(self):
        store = InMemoryRunStore()
        record = RunRecord(pipeline_run_id="r-fail", status=LeanRunStatus.FAILED, created_at="2026-06-07T00:00:00Z", error="pipe blew up")
        _seed(store, record)
        client = _build_client(store)
        response = client.get("/api/v1/runs/by-id/r-fail/result")
        assert response.status_code == 409
        assert "pipe blew up" in response.json()["detail"]["message"]

    def test_result_route_unknown_returns_404(self):
        client = _build_client(InMemoryRunStore())
        response = client.get("/api/v1/runs/by-id/ghost/result")
        assert response.status_code == 404
        assert response.json()["detail"]["error_type"] == "RunNotFound"

    def test_status_route_returns_record(self):
        store = InMemoryRunStore()
        _seed(store, RunRecord(pipeline_run_id="r-stat", status=LeanRunStatus.RUNNING, created_at="2026-06-07T00:00:00Z"))
        client = _build_client(store)
        response = client.get("/api/v1/runs/by-id/r-stat")
        assert response.status_code == 200
        body = response.json()
        assert body["pipeline_run_id"] == "r-stat"
        assert body["status"] == "RUNNING"

    def test_start_creates_record_and_returns_id(self, mocker: MockerFixture):
        store = InMemoryRunStore()
        # Don't actually execute — just confirm start registers a record and returns its id.
        mocker.patch.object(runs_module, "_run_in_background", new=mocker.AsyncMock(return_value=None))
        client = _build_client(store)
        response = client.post(
            "/api/v1/runs",
            json={"pipe_code": "echo", "mthds_contents": [_VALID_MTHDS], "inputs": {"text": "hi"}},
        )
        assert response.status_code == 200
        run_id = response.json()["pipeline_run_id"]
        assert run_id
        assert response.json()["status"] in ("PENDING", "RUNNING")
        assert run_id in store._records  # pyright: ignore[reportPrivateUsage]
