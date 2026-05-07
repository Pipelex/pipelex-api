"""Smoke tests for /health and /version endpoints + request-body size middleware."""

from importlib.metadata import PackageNotFoundError

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from starlette.middleware.base import BaseHTTPMiddleware

from api.middleware import request_body_size_middleware
from api.routes.health import router as health_router
from api.routes.version import router as version_router


def _build_client_with_body_cap() -> TestClient:
    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=request_body_size_middleware)
    app.include_router(health_router)
    app.include_router(version_router, prefix="/api/v1")
    return TestClient(app)


class TestSimpleRoutes:
    def test_health(self):
        client = _build_client_with_body_cap()
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "message": "Pipelex API is running"}

    def test_pipelex_version(self):
        client = _build_client_with_body_cap()
        response = client.get("/api/v1/pipelex_version")
        assert response.status_code == 200
        assert "version" in response.json()
        assert response.json()["version"]

    def test_api_version(self):
        client = _build_client_with_body_cap()
        response = client.get("/api/v1/api_version")
        assert response.status_code == 200
        assert "version" in response.json()
        assert response.json()["version"]

    def test_pipelex_version_handles_missing_metadata(self, mocker: MockerFixture):
        mocker.patch("api.routes.version.version", side_effect=PackageNotFoundError("pipelex"))
        client = _build_client_with_body_cap()
        response = client.get("/api/v1/pipelex_version")
        assert response.status_code == 500
        assert response.json()["detail"]["error_type"] == "PackageNotFound"

    def test_request_body_size_rejects_oversized_via_content_length(self):
        client = _build_client_with_body_cap()
        # 200 MiB declared > 100 MiB default cap.
        response = client.get("/health", headers={"content-length": str(200 * 1024 * 1024)})
        assert response.status_code == 413
        assert response.json()["detail"]["error_type"] == "PayloadTooLarge"
