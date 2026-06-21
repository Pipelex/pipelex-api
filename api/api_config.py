"""Pipelex-API deployment config: the top-level execution mode + override policy.

The runner is orchestrator-agnostic. WHICH mode a top-level ``POST /start``
dispatches as — ``direct`` in-process (the base default), Temporal
fire-and-forget, Mistral-native, … — is a *deployment* choice, never a property
of this open-source base. It is read from a packaged ``api.toml`` (keys at the
file root — no ``[api]`` wrapper, since :meth:`load_plugin_config` validates the
whole document against the schema, exactly like ``temporal.toml``), env-layered
like the main pipelex config and every plugin config (D2): the packaged default
``api.toml`` (shipped in this wheel) is deep-merged with the env-selected
``api_{environment}.toml`` and ``api_override.toml`` from ``~/.pipelex`` then the
project ``.pipelex``, with ``PIPELEX_ENV`` (``runtime_manager.environment``)
choosing the env file. One image bakes every env file; a deployment flavor (e.g.
``pipelex-api-hosted``) bakes ``.pipelex/api_{env}.toml`` to flip the default.
The base names no orchestrator and ships ``execution_mode = "direct"``.

Why a separate ``api.toml`` and not the core ``pipelex_{env}.toml``: core's
config is ``extra="forbid"``, so an ``[api]`` section there is rejected at load.
Loading it via core's reusable :meth:`load_plugin_config` keeps this a pure
pipelex-api concern while reusing the identical env-layering machinery — and
keeps it symmetric with how ``pipelex-temporal`` self-loads ``temporal.toml``.
"""

from functools import cache
from pathlib import Path

from pipelex.runtime_bridge.execution_mode import PipelexExecutionMode
from pipelex.system.configuration.config_loader import config_manager
from pydantic import BaseModel, ConfigDict, field_validator

from api.error_types import ErrorType
from api.errors import raise_forbidden

API_CONFIG_NAME = "api"

# The packaged default ``api.toml`` ships in the wheel alongside this module.
_PACKAGE_DIR = Path(__file__).resolve().parent


class ApiConfig(BaseModel):
    """The ``[api]`` deployment config: default execution mode + override policy.

    No field defaults — the packaged ``api.toml`` is the single source of the
    base defaults (mirroring core's "defaults live in the TOML, never in the
    model" discipline). ``extra="forbid"`` so a typo'd key in a baked override
    fails loud at load instead of being silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    execution_mode: PipelexExecutionMode
    allow_request_execution_mode_override: bool

    @field_validator("execution_mode")
    @classmethod
    def _reject_fire_and_forget_default(cls, value: PipelexExecutionMode) -> PipelexExecutionMode:
        """``execution_mode`` names the deployment's SYNCHRONOUS backend, never a fire-and-forget one.

        Fire-and-forget is derived per-endpoint, not configured: ``/start`` derives the f&f sibling
        of this mode while ``/execute`` and ``/validate`` dispatch it as-is. A *configured* f&f mode
        would silently half-break the deployment — every ``/execute`` would ``400``
        (``FireAndForgetNotSupported``) and ``/validate`` would dispatch f&f to the validator
        registry. Reject it at load so a baked override fails fast (matching ``extra="forbid"``)
        instead of booting a broken deployment. A caller may still *request* f&f per request on
        ``/start`` — that path is gated by the override policy, not by this field.
        """
        if value.is_fire_and_forget:
            msg = (
                f"execution_mode '{value}' is fire-and-forget, which is derived per-endpoint, not configured. "
                f"Set the synchronous backend instead (e.g. 'temporal_blocking') — '/start' derives its "
                f"fire-and-forget variant."
            )
            raise ValueError(msg)
        return value


def load_api_config() -> ApiConfig:
    """Load the ``[api]`` config from ``api.toml`` with env-aware layering (D2).

    Delegates to core's reusable plugin-config loader: the packaged ``api.toml``
    is deep-merged with the env-selected overrides. The packaged default alone is
    a valid, fully-resolved config — every override tier is optional. Requires
    Pipelex to be booted (``runtime_manager.environment`` must be resolved), so
    it is called only after ``Pipelex.make`` — never at import.
    """
    return config_manager.load_plugin_config(name=API_CONFIG_NAME, package_dir=_PACKAGE_DIR, schema=ApiConfig)


@cache
def get_api_config() -> ApiConfig:
    """Process-cached :class:`ApiConfig`.

    The config is immutable for the life of the process (``PIPELEX_ENV`` is fixed
    at boot), so it is loaded once and cached. ``api.main`` warms this at startup
    so a malformed ``api.toml`` / baked override fails the app fast — the same
    fail-fast posture as ``ERROR_DISCLOSURE``. Tests that need a different mode
    patch this getter (or call :func:`resolve_execution_mode` with a hand-built
    config) rather than mutating the cache.
    """
    return load_api_config()


def resolve_execution_mode(requested: PipelexExecutionMode | None, *, config: ApiConfig) -> PipelexExecutionMode:
    """Resolve the effective execution mode for a top-level run, applying policy.

    The deployment default (``config.execution_mode``) wins unless the caller
    supplied a *different* mode AND the deployment opted into per-request
    override (``allow_request_execution_mode_override``). A caller-supplied mode
    equal to the default is always honored (it changes nothing). A caller trying
    to FORCE a different mode on a runner whose policy forbids it is refused with
    a 403 — so a locked-down Temporal runner can never be coerced into ``direct``
    (whose whole point would be to bypass distributed execution), and vice versa.
    """
    if requested is None or requested == config.execution_mode:
        return config.execution_mode
    if config.allow_request_execution_mode_override:
        return requested
    msg = (
        f"This deployment does not allow overriding execution_mode per request (configured mode '{config.execution_mode}', requested '{requested}')."
    )
    raise_forbidden(msg, error_type=ErrorType.EXECUTION_MODE_OVERRIDE_FORBIDDEN)
