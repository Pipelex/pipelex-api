import pytest
from pipelex.pipelex import Pipelex
from pipelex.system.runtime import IntegrationMode
from pipelex.test_extras.shared_pytest_plugins import needs_inference_in_pipelex
from pytest import FixtureRequest

from api.api_config import get_api_config


@pytest.fixture(autouse=True)
def reset_api_config_fixture(request: FixtureRequest):
    # Code to run before each test
    print("\n[magenta] Api setup[/magenta]")
    # The base runner is orchestrator-agnostic: with no orchestrator plugin installed and no
    # `boot_orchestrator` set, every pipeline (incl. dry-run validation) runs DIRECT in-process,
    # which is exactly what the hermetic suite needs. The former `temporal_enabled=False` knob is
    # gone — Temporal is now an external plugin, absent from this repo's deps entirely.
    pipelex_instance = Pipelex.make(
        integration_mode=IntegrationMode.PYTEST,
        needs_inference=needs_inference_in_pipelex(request),
    )
    # Drop the process-cached `[api]` config so a test that patches its env / loader cannot leak a
    # mutated config into later tests through the `@cache`d `get_api_config()` (the suite otherwise
    # relies on the packaged `direct` default — e.g. the `POST /start` override-policy 403 test).
    get_api_config.cache_clear()
    yield
    # Code to run after each test
    print("\n[magenta] Api teardown[/magenta]")
    get_api_config.cache_clear()
    pipelex_instance.teardown()
