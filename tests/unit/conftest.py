import pytest
from pipelex.pipelex import Pipelex
from pipelex.system.runtime import IntegrationMode
from pipelex.test_extras.shared_pytest_plugins import needs_inference_in_pipelex
from pytest import FixtureRequest


@pytest.fixture(autouse=True)
def reset_api_config_fixture(request: FixtureRequest):
    # Code to run before each test
    print("\n[magenta] Api setup[/magenta]")
    pipelex_instance = Pipelex.make(
        IntegrationMode.PYTEST,
        needs_inference=needs_inference_in_pipelex(request),
        # Force Temporal off so the suite is hermetic and runs pipelines (incl. dry-run validation)
        # in-process. Temporal is opt-in via a gitignored .pipelex/pipelex_override.toml; CI has no
        # such override, so this just pins local runs to the CI configuration. Tests that exercise the
        # Temporal-backed routes mock the runner, so none need a live server.
        temporal_enabled=False,
    )
    yield
    # Code to run after each test
    print("\n[magenta] Api teardown[/magenta]")
    pipelex_instance.teardown()
