"""FastAPI app init, middleware, and router registration.

App construction lives here; the failure → HTTP response mapping lives in
`api.exception_handlers` (registered against this app below). Routes
therefore no longer need to catch and shape errors themselves — anything
they raise lands in the right handler by exception class.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mthds.protocol.protocol import PROTOCOL_VERSION
from pipelex.pipelex import Pipelex
from pipelex.plugins.discovery import build_registrar
from pipelex.plugins.registrar import HttpErrorMapperFn
from pipelex.system.configuration.config_loader import config_manager
from pipelex.system.configuration.configs import PipelexConfig
from pipelex.system.environment import get_optional_env
from pipelex.system.runtime import IntegrationMode
from starlette.middleware.base import BaseHTTPMiddleware

from api.api_config import get_api_config, resolve_boot_orchestrator
from api.disclosure import resolve_disclosure_mode
from api.exception_handlers import register_exception_handlers
from api.middleware import RequestIdMiddleware, request_body_size_middleware
from api.routes import router as api_router
from api.routes.health import router as health_router
from api.routes.version import router as version_router
from api.security import get_auth_dependency


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    # Resolve the deployment's orchestration mode BEFORE booting, so the process can boot
    # under the matching orchestrator. `orchestration_mode` selects the dispatch arm; a
    # non-`direct` (async/boot) orchestrator — e.g. "temporal" — must additionally claim the
    # process-global execution hub slots at boot, which the shared WorkflowExecutor requires
    # (`is_*_boot_active()`): without it, a `temporal`-mode runner resolves the Temporal
    # dispatch arm but the underlying pipe-run stack is still in-process, and dispatch fails
    # with AsyncExecutionNotEnabledError. The base `direct` mode names no orchestrator and
    # boots in-process (boot_orchestrator=None). `resolve_boot_orchestrator` derives boot from
    # the deployment config and refuses a config whose single boot can't service its own
    # per-request override policy (a `direct` default with override on) — so boot and dispatch
    # can never be set inconsistently.
    #
    # Loading `api.toml` here also fails the app fast on a malformed config / baked override
    # (the same posture as ERROR_DISCLOSURE), now even before the singleton exists. The loader
    # only needs `runtime_manager.environment` (from PIPELEX_ENV), which resolves without a
    # live singleton. get_api_config() is @cache'd, so the warm here is reused everywhere.
    boot_orchestrator = resolve_boot_orchestrator(get_api_config())
    Pipelex.make(integration_mode=IntegrationMode.FASTAPI, boot_orchestrator=boot_orchestrator)
    try:
        yield
    finally:
        Pipelex.teardown_if_needed()


def _resolve_cors_origins() -> tuple[list[str], bool]:
    """Read CORS_ALLOW_ORIGINS env var. Returns (origins, allow_credentials).

    Default: wildcard origins, credentials disabled — the only valid combination
    when origins is `*` (browsers reject credentials with wildcard). To enable
    credentials, set CORS_ALLOW_ORIGINS to a comma-separated allowlist.
    """
    raw = get_optional_env("CORS_ALLOW_ORIGINS")
    if not raw or raw.strip() == "*":
        return ["*"], False
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if not origins:
        return ["*"], False
    return origins, True


# Resolve and validate ERROR_DISCLOSURE once, at module/startup: an unrecognized
# value raises here and the production app fails to boot rather than silently
# defaulting. Passed into `register_exception_handlers` below; the resolved
# mode drives how much of an error report reaches a client. Kept module-level
# so the production fail-fast lives on this single import path — only this
# module triggers it, not `api.exception_handlers` (which lets tests register
# the handlers without inheriting the env-validation crash).
ERROR_DISCLOSURE_MODE = resolve_disclosure_mode()


def _resolve_http_error_mappers() -> dict[type[Exception], HttpErrorMapperFn]:
    """Resolve the orchestrator plugins' HTTP-error mappers for this deployment.

    Runs the pure, repeatable `build_registrar` against the loaded config (the same
    standalone pattern `pipelex plugins list` uses) and reads back the
    `{exc_type: to_error_report}` map each installed orchestrator plugin contributed
    via `PluginRegistrar.add_http_error_mapper`. The base installs no orchestrator
    plugin, so this is an empty map and no transport-error handler is registered; a
    flavor (e.g. the Temporal one) contributes its plugin's mapper here, and only at
    this point — at app construction, where the plugin (and therefore its SDK) is by
    definition installed — is the plugin's exc-type provider thunk run. Resolved once
    at module import so a duplicate/broken plugin fails the app fast, mirroring the
    `ERROR_DISCLOSURE` fail-fast above.

    Safe at import because the map is a pure function of *installed* plugins (entry
    points + `config.plugins.disabled`) — never of the `boot_orchestrator` that
    `Pipelex.make` selects at boot — so an import-time resolution yields the identical
    map a post-boot one would.
    """
    config = PipelexConfig.model_validate(config_manager.load_config())
    return build_registrar(config=config).get_http_error_mappers()


HTTP_ERROR_MAPPERS = _resolve_http_error_mappers()


def _own_version() -> str:
    """This server package's version — best-effort for app metadata."""
    try:
        return package_version("pipelex-api")
    except PackageNotFoundError:
        return "0.0.0"


fastapi_app = FastAPI(
    redirect_slashes=False,
    lifespan=lifespan,
    title="Pipelex API",
    version=_own_version(),
    summary=f"The open-source Pipelex runner — implements MTHDS Protocol v{PROTOCOL_VERSION}.",
    description=(
        f"This server implements the [MTHDS Protocol](https://mthds.ai) v{PROTOCOL_VERSION} "
        "(`POST /execute`, `POST /start`, `POST /validate`, `GET /models`, `GET /version` — "
        "marked `x-mthds-protocol: true`) plus the Pipelex build tooling extensions (`/build/*`) "
        "and editor tooling extensions (`/lint`, `/format`). "
        "Contract layering: MTHDS Protocol ⊂ Pipelex API (this server) ⊂ Pipelex hosted API. "
        "Routes not in the published contract (`/upload`, `/resolve-storage-url`) are documented "
        "as non-contract in their descriptions. All endpoints are served under the `/v1` base path; "
        "errors are RFC 7807 `application/problem+json`."
    ),
    license_info={"name": "MIT", "identifier": "MIT"},
)

# Order matters: Starlette's `add_middleware` PREPENDS (see
# `user_middleware.insert(0, ...)` in `starlette.applications`), so the LAST
# `add_middleware` call becomes the OUTERMOST wrapper. Body-size is registered
# first so CORS ends up wrapping it: a 413 short-circuit from the body-size
# middleware still passes back through CORSMiddleware on the way out, so a
# cross-origin browser POST sees the RFC 7807 413 with the
# `Access-Control-Allow-Origin` header it needs — not a generic CORS error
# that swallows the response.
fastapi_app.add_middleware(BaseHTTPMiddleware, dispatch=request_body_size_middleware)

cors_origins, cors_allow_credentials = _resolve_cors_origins()
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

fastapi_app.include_router(health_router)

# `GET /v1/version` is the protocol handshake — ALWAYS public, mounted without
# the auth dependency exactly like `/health` (clients call it for feature
# detection before they have credentials).
fastapi_app.include_router(version_router, prefix="/v1")

# Register all other routes WITH authentication (auto-selects based on AUTH_MODE env var: none/jwt/api_key).
# The API mounts at `/v1` — the SDKs compose `{MTHDS_API_URL}/v1/{endpoint}` (master D10); no `/api/v1`
# mount and no alias remain.
auth_dependency = get_auth_dependency()
fastapi_app.include_router(api_router, prefix="/v1", dependencies=[Depends(auth_dependency)])


@fastapi_app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Pipelex API"}


register_exception_handlers(fastapi_app, disclosure_mode=ERROR_DISCLOSURE_MODE, http_error_mappers=HTTP_ERROR_MAPPERS)


# RequestIdMiddleware wraps the *entire* FastAPI app — including Starlette's
# ServerErrorMiddleware, which `add_middleware` could only ever nest inside.
# This is what makes it genuinely outermost: the request-id contextvars are
# bound, and `X-Request-ID` is echoed, on every response — the catch-all 500
# included. `app` is the ASGI entrypoint (uvicorn loads `api.main:app`).
app = RequestIdMiddleware(fastapi_app)
