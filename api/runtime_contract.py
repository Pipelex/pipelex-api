"""What this server needs from the `pipelex` it resolved, asserted once at boot.

This exists because of a gap the packaging cannot close on its own. While the structured-log seam is
unreleased, `pyproject.toml` resolves `pipelex` from a git source declared in `[tool.uv.sources]`.
Every install path this repository has goes through `uv`, which honours that entry — `make install`,
CI's `uv lock --check`, and the Docker build's `uv sync --frozen`. A plain `pip install .` does not
read it, and the branch build declares the same version number as the published release, so **no
version specifier can distinguish them**: such an install silently resolves the published `pipelex`,
which has none of this.

Without a check, the first symptom is a `TypeError` raised from inside the error handler, on the
first request that fails — the worst place to learn about it, because the failure being reported is
lost behind the failure to report it. One assertion at boot turns that into a refusal that names the
cause.

The check goes away with the pin: once `pipelex` is an exact published version again, the version
specifier does the work and this module is deleted with the `[tool.uv.sources]` section.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

from pipelex import log

_RUNTIME_PACKAGE = "pipelex"


class RuntimeContractError(RuntimeError):
    """The resolved `pipelex` cannot carry this server's logs, so the boot refuses rather than limps."""


def _installed_runtime_version() -> str:
    try:
        return package_version(_RUNTIME_PACKAGE)
    except PackageNotFoundError:
        return "not installed"


def assert_the_runtime_carries_the_structured_log_seam(*, log_facade: Any = log) -> None:
    """Refuse to boot on a `pipelex` without the request-scoped log context.

    `log.context` is the sharpest single probe: it arrived with the seam, and a runtime that has it
    has the per-call `fields=` and the sink registry too, because they are one change. Testing for it
    is also cheap enough to sit on the boot path without apology.
    """
    if hasattr(log_facade, "context"):
        return

    installed = _installed_runtime_version()
    msg = (
        f"the installed {_RUNTIME_PACKAGE} ({installed}) has no `log.context`, so it predates the structured-log seam "
        "this server is built on: its error handler would raise on the first failure it tried to report. "
        f"The version number cannot tell you which build you have — the branch this server pins declares the same one "
        f"as the published release. Most likely a `pip install .` resolved {_RUNTIME_PACKAGE} from PyPI: `pip` does not "
        "read the `[tool.uv.sources]` entry in pyproject.toml that points at the branch, and `uv` does. "
        "Install with `make install`, or with `uv sync`, until that pin collapses to a published version."
    )
    raise RuntimeContractError(msg)
