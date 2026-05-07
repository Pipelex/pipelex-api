import os

# Set placeholder values for required env vars BEFORE any test module is collected,
# because some api modules (e.g. api/routes/pipelex/pipeline.py) call get_required_env()
# at import time. Conftest loads before test collection, so this runs early enough.
# Tests that exercise these vars must set/override them explicitly.
os.environ.setdefault("COMPLETION_CALLBACK_SECRET", "test-placeholder-completion-callback-secret")

pytest_plugins = [
    "pipelex.test_extras.shared_pytest_plugins",
]
