"""The server refuses to start on a `pipelex` that cannot carry its structured logs.

While the structured-log seam is unreleased, `pyproject.toml` resolves `pipelex` from a git source
through `[tool.uv.sources]`. Every install path this repository has goes through `uv`, which honours
that entry — but a plain `pip install .` does not, and the branch build declares a version number the
published releases already carry, so no version specifier can select it. Such an install gets a
`pipelex` with no `log.context` and no `fields=`, which refuses this server's `sink` key at the first
config read, or, where that key is not on the config path, raises from inside the error handler. The
check therefore has to run at import, above that first read.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.runtime_contract import (
    RuntimeContractError,
    assert_the_runtime_carries_the_structured_log_seam,
)


class TestRuntimeContract:
    def test_the_installed_runtime_satisfies_the_contract(self):
        # The real `pipelex` this checkout resolved. Green here is the whole point of the pin.
        assert_the_runtime_carries_the_structured_log_seam()

    def test_a_runtime_without_the_log_context_is_refused_by_name(self):
        # What a published 0.59.0 looks like from here: `log` exists, the log calls exist, and the
        # request-scoped context does not.
        without_context = SimpleNamespace(info=print, warning=print, error=print)

        with pytest.raises(RuntimeContractError) as caught:
            assert_the_runtime_carries_the_structured_log_seam(log_facade=without_context)

        message = str(caught.value)
        assert "log.context" in message
        assert "pip install" in message, "the message has to name the install path that produces this"

    def test_the_refusal_names_the_version_that_cannot_be_told_apart(self):
        without_context = SimpleNamespace(info=print, warning=print, error=print)

        with pytest.raises(RuntimeContractError) as caught:
            assert_the_runtime_carries_the_structured_log_seam(log_facade=without_context)

        # Naming the installed version is what makes the report actionable, since the version alone
        # does not distinguish the branch build from the published one.
        assert "pipelex" in str(caught.value)

    def test_the_server_checks_the_runtime_before_any_module_level_code_runs(self):
        # `api.main` validates the config at import (`HTTP_ERROR_MAPPERS`), and a runtime without the
        # seam refuses this server's `sink` key right there. A check placed anywhere later — in
        # `lifespan`, or below that assignment — is never reached on the install it exists for, so the
        # call has to be the first module-level statement after the imports.
        main_source = (Path(__file__).resolve().parents[2] / "api" / "main.py").read_text(encoding="utf-8")
        statements = [
            statement
            for statement in ast.parse(main_source).body
            if not isinstance(statement, (ast.Import, ast.ImportFrom))
            and not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        ]
        first = statements[0]
        assert isinstance(first, ast.Expr)
        assert isinstance(first.value, ast.Call)
        assert isinstance(first.value.func, ast.Name)
        assert first.value.func.id == assert_the_runtime_carries_the_structured_log_seam.__name__
