"""Route-level tests for the method-bundle transport on /execute and /start.

The pipeline runner is mocked (as in `test_pipeline_routes`); these assert the
API layer materializes a request-carried bundle into a temporary `library_dirs`
directory, that the materialized files are present on disk when the runner runs,
that a bundle shipping custom Python is gated on a sandbox-hosted deployment,
and that the both-forms guard surfaces as a 422.
"""

import base64
import io
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.pipeline.pipeline_response import PipelexRunResultStart, RunState
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.routes.pipelex.pipeline import router as pipeline_router
from tests.unit._constants import VALID_MTHDS

_PIPE_FUNC_PY = "def echo(working_memory):\n    return 'hi'\n"


def _zip_b64(files: dict[str, str]) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _build_client(mocker: MockerFixture) -> tuple[TestClient, dict[str, Any]]:
    """Wire an app whose ApiRunner is mocked and records what the run actually saw.

    The `snapshot` dict captures the `library_dirs` the runner was constructed
    with and the files present on disk at execute/start time — proving the
    bundle was materialized before the run and is torn down after.
    """
    app = FastAPI()
    app.include_router(pipeline_router, prefix="/v1")
    register_exception_handlers(app)

    snapshot: dict[str, Any] = {}

    fake_execute_response = mocker.MagicMock()
    fake_execute_response.model_dump.return_value = {"pipeline_run_id": "run-1", "state": "COMPLETED"}

    def _record(library_dirs: list[str] | None) -> None:
        snapshot["library_dirs"] = library_dirs
        if library_dirs:
            root = Path(library_dirs[0])
            snapshot["dir_exists"] = root.exists()
            snapshot["files"] = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())

    def _make_runner(**kwargs: Any) -> Any:
        library_dirs: list[str] | None = kwargs.get("library_dirs")
        runner = mocker.MagicMock()

        async def _execute(**_kwargs: Any) -> Any:
            _record(library_dirs)
            return fake_execute_response

        async def _start(**_kwargs: Any) -> Any:
            _record(library_dirs)
            return PipelexRunResultStart(
                pipeline_run_id="run-1",
                created_at="2026-01-15T12:00:00Z",
                state=RunState.STARTED,
                workflow_id="wf-1",
            )

        runner.execute = _execute
        runner.start = _start
        return runner

    mocker.patch("api.routes.pipelex.pipeline.ApiRunner", side_effect=_make_runner)
    return TestClient(app), snapshot


class TestPipelineBundle:
    def test_files_bundle_reaches_runner(self, mocker: MockerFixture):
        client, snapshot = _build_client(mocker)
        response = client.post("/v1/execute", json={"files": {"main.mthds": VALID_MTHDS}, "inputs": {"text": "hi"}})
        assert response.status_code == 200
        assert snapshot["dir_exists"] is True
        assert snapshot["files"] == ["main.mthds"]
        # Temp dir is cleaned once the request returns.
        assert not Path(snapshot["library_dirs"][0]).exists()

    def test_zip_bundle_reaches_runner(self, mocker: MockerFixture):
        client, snapshot = _build_client(mocker)
        bundle = _zip_b64({"main.mthds": VALID_MTHDS})
        response = client.post("/v1/execute", json={"bundle_b64": bundle, "inputs": {"text": "hi"}})
        assert response.status_code == 200
        assert snapshot["files"] == ["main.mthds"]

    def test_no_bundle_leaves_library_dirs_none(self, mocker: MockerFixture):
        client, snapshot = _build_client(mocker)
        response = client.post("/v1/execute", json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "inputs": {}})
        assert response.status_code == 200
        assert snapshot["library_dirs"] is None

    def test_python_bundle_forbidden_when_not_hosted(self, mocker: MockerFixture):
        mocker.patch("api.routes.pipelex.pipeline.is_pipe_func_sandbox_hosted", return_value=False)
        client, _ = _build_client(mocker)
        response = client.post("/v1/execute", json={"files": {"main.mthds": VALID_MTHDS, "pipe_func.py": _PIPE_FUNC_PY}})
        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "CustomCodeRequiresSandbox"

    def test_python_bundle_allowed_when_hosted(self, mocker: MockerFixture):
        mocker.patch("api.routes.pipelex.pipeline.is_pipe_func_sandbox_hosted", return_value=True)
        client, snapshot = _build_client(mocker)
        response = client.post("/v1/execute", json={"files": {"main.mthds": VALID_MTHDS, "pipe_func.py": _PIPE_FUNC_PY}})
        assert response.status_code == 200
        assert snapshot["files"] == ["main.mthds", "pipe_func.py"]

    def test_both_forms_rejected_via_route(self, mocker: MockerFixture):
        client, _ = _build_client(mocker)
        response = client.post(
            "/v1/execute",
            json={"files": {"main.mthds": VALID_MTHDS}, "bundle_b64": _zip_b64({"main.mthds": VALID_MTHDS})},
        )
        assert response.status_code == 422
        assert response.json()["error_type"] == "InvalidBundle"

    def test_start_materializes_bundle(self, mocker: MockerFixture):
        client, snapshot = _build_client(mocker)
        response = client.post("/v1/start", json={"files": {"main.mthds": VALID_MTHDS}, "inputs": {"text": "hi"}})
        assert response.status_code == 202
        assert snapshot["files"] == ["main.mthds"]

    def test_bundle_and_mthds_contents_are_mutually_exclusive(self, mocker: MockerFixture):
        client, _ = _build_client(mocker)
        response = client.post(
            "/v1/execute",
            json={"files": {"main.mthds": VALID_MTHDS}, "mthds_contents": [VALID_MTHDS]},
        )
        assert response.status_code == 422
        assert "mutually exclusive" in response.text
