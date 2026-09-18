"""The boot refuses a `pipelex` that cannot carry this server's structured logs.

While the structured-log seam is unreleased, `pyproject.toml` resolves `pipelex` from a git source
through `[tool.uv.sources]`. Every install path this repository has goes through `uv`, which honours
that entry — but a plain `pip install .` does not, and the branch build declares the same version
number as the published release, so no version specifier can tell them apart. Such an install gets a
`pipelex` with no `log.context` and no `fields=`, and without this check the first thing it does is
raise from inside the error handler, which is the worst possible place to learn about it.
"""

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
