"""`[api]` deployment config — the default execution mode + the per-request override policy.

The orchestrator-agnostic base reads WHICH mode a top-level `POST /start` dispatches as from a
packaged `api.toml` (`ApiConfig`), and gates per-request overrides behind a deployment policy. These
tests pin the packaged default (DIRECT, override off) and the resolver's policy: the default wins, a
caller may only change the mode when the deployment opted in, and a forbidden override is a 403 —
asserted both at the resolver and end-to-end on `POST /start`.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.runtime_bridge.execution_mode import PipelexExecutionMode

from api.api_config import ApiConfig, get_api_config, resolve_execution_mode
from api.errors import ApiError
from api.exception_handlers import register_exception_handlers
from api.middleware import RequestIdMiddleware
from api.routes.pipelex.pipeline import router as pipeline_router
from tests.unit._constants import VALID_MTHDS


def _temporal_locked_config() -> ApiConfig:
    """A hosted-style config: Temporal fire-and-forget default, override OFF."""
    return ApiConfig(execution_mode=PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET, allow_request_execution_mode_override=False)


class TestApiConfigDefault:
    def test_packaged_default_is_direct_no_override(self):
        # The open-source base names no orchestrator: it ships `direct` and refuses overrides.
        config = get_api_config()
        assert config.execution_mode is PipelexExecutionMode.DIRECT
        assert config.allow_request_execution_mode_override is False


class TestResolveExecutionMode:
    def test_none_request_uses_deployment_default(self):
        assert resolve_execution_mode(None, config=_temporal_locked_config()) is PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET

    def test_request_equal_to_default_is_honored(self):
        # A no-op override (same as the default) is always accepted, override policy or not.
        config = _temporal_locked_config()
        assert resolve_execution_mode(PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET, config=config) is PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET

    def test_forbidden_override_is_refused(self):
        # A caller must not be able to force `direct` on a locked-down distributed runner.
        with pytest.raises(ApiError) as exc_info:
            resolve_execution_mode(PipelexExecutionMode.DIRECT, config=_temporal_locked_config())
        assert exc_info.value.status_code == 403
        assert exc_info.value.document["error_type"] == "ExecutionModeOverrideForbidden"

    def test_allowed_override_is_honored(self):
        config = ApiConfig(execution_mode=PipelexExecutionMode.TEMPORAL_FIRE_AND_FORGET, allow_request_execution_mode_override=True)
        assert resolve_execution_mode(PipelexExecutionMode.DIRECT, config=config) is PipelexExecutionMode.DIRECT


class TestStartOverridePolicyEndToEnd:
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(pipeline_router, prefix="/v1")
        register_exception_handlers(app)
        return TestClient(RequestIdMiddleware(app))

    def test_forbidden_per_request_mode_on_start_is_403(self):
        # The base config is DIRECT with override off, so a caller forcing a different mode on
        # `POST /start` is refused with a 403 BEFORE any library load / dispatch — the policy gate
        # is the first thing `ApiRunner.start` checks.
        response = self._client().post(
            "/v1/start",
            json={"pipe_code": "echo", "mthds_contents": [VALID_MTHDS], "execution_mode": "temporal_fire_and_forget"},
        )
        assert response.status_code == 403, response.text
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["error_type"] == "ExecutionModeOverrideForbidden"
