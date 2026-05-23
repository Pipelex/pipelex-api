"""Smoke + validation tests for /validate, /build/* and /build/{concept,pipe-spec,models}."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from api.main import register_exception_handlers
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
