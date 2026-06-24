"""`[api]` deployment config — the default orchestration mode + the per-request override policy.

The orchestrator-agnostic base reads WHICH backend a top-level run dispatches to from a packaged
`api.toml` (`ApiConfig`), and gates per-request overrides behind a deployment policy. `orchestration_mode`
is an OPEN string token (core owns `"direct"`; each plugin owns its own); the delivery axis is
endpoint-set, never configured. These tests pin the packaged default (`direct`, override off) and the
resolver's policy: the default wins, a caller may only change the backend when the deployment opted in,
and a forbidden override is a 403 — asserted both at the resolver and end-to-end on `POST /start`.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.api_config import ApiConfig, get_api_config, resolve_orchestration_mode
from api.errors import ApiError
from api.exception_handlers import register_exception_handlers
from api.middleware import RequestIdMiddleware
from api.routes.pipelex.pipeline import router as pipeline_router
from tests.unit._constants import VALID_MTHDS


def _temporal_locked_config() -> ApiConfig:
    """A hosted-style config: the `temporal` backend, override OFF.

    `orchestration_mode` names only the backend; the delivery axis (blocking vs fire-and-forget) is
    endpoint-set, never configured, so there is no fire-and-forget token to reject here.
    """
    return ApiConfig(orchestration_mode="temporal", allow_request_orchestration_mode_override=False)


class TestApiConfigDefault:
    def test_packaged_default_is_direct_no_override(self):
        # The open-source base names no orchestrator: it ships `direct` and refuses overrides.
        config = get_api_config()
        assert config.orchestration_mode == "direct"
        assert config.allow_request_orchestration_mode_override is False


class TestResolveOrchestrationMode:
    def test_none_request_uses_deployment_default(self):
        assert resolve_orchestration_mode(None, config=_temporal_locked_config()) == "temporal"

    def test_request_equal_to_default_is_honored(self):
        # A no-op override (same as the default) is always accepted, override policy or not.
        config = _temporal_locked_config()
        assert resolve_orchestration_mode("temporal", config=config) == "temporal"

    def test_forbidden_override_is_refused(self):
        # A caller must not be able to force `direct` on a locked-down distributed runner.
        with pytest.raises(ApiError) as exc_info:
            resolve_orchestration_mode("direct", config=_temporal_locked_config())
        assert exc_info.value.status_code == 403
        assert exc_info.value.document["error_type"] == "OrchestrationModeOverrideForbidden"

    def test_allowed_override_is_honored(self):
        config = ApiConfig(orchestration_mode="temporal", allow_request_orchestration_mode_override=True)
        assert resolve_orchestration_mode("direct", config=config) == "direct"


class TestStartOverridePolicyEndToEnd:
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(pipeline_router, prefix="/v1")
        register_exception_handlers(app)
        return TestClient(RequestIdMiddleware(app))

    def test_forbidden_per_request_mode_on_start_is_403(self):
        # The base config is `direct` with override off, so a caller forcing a different backend on
        # `POST /start` is refused with a 403 BEFORE any library load / dispatch — the policy gate
        # is the first thing `ApiRunner.start` checks.
        response = self._client().post(
            "/v1/start",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "orchestration_mode": "temporal"},
        )
        assert response.status_code == 403, response.text
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["error_type"] == "OrchestrationModeOverrideForbidden"
