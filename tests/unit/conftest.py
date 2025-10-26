import pytest
from pipelex.pipelex import Pipelex
from pipelex.system.runtime import IntegrationMode


@pytest.fixture(autouse=True)
def reset_api_config_fixture():
    # Code to run before each test
    print("\n[magenta] Api setup[/magenta]")
    pipelex_instance = Pipelex.make(IntegrationMode.PYTEST)
    yield
    # Code to run after each test
    print("\n[magenta] Api teardown[/magenta]")
    pipelex_instance.teardown()
