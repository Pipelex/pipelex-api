from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pipelex.pipelex import Pipelex
from pipelex.system.environment import get_optional_env
from pipelex.system.runtime import IntegrationMode
from starlette.middleware.base import BaseHTTPMiddleware

from api.middleware import request_body_size_middleware
from api.routes import router as api_router
from api.routes.health import router as health_router
from api.run_store import make_run_store
from api.security import get_auth_dependency

# The runner contract — the open-source, self-hostable standard. See docs/index.md
# and docs/openapi.json. The hosted Pipelex API namespaces this same surface as
# `/runner/v1/*` behind its gateway; self-hosters reach it directly at `/api/v1/*`.
API_TITLE = "Pipelex Runner API"
API_DESCRIPTION = """\
The **Pipelex Runner API** is the open-source, self-hostable engine that executes
Pipelex pipelines (MTHDS bundles) over HTTP. It is **stateless**: it needs no
database, queue, or object store to run pipelines — boot the Docker image, give it
an inference backend, and call `/api/v1/pipeline/execute`.

### What you must provide
- **Inference backend** — by default a `PIPELEX_GATEWAY_API_KEY` (the Pipelex Gateway
  routes to every supported model with one credential). To call providers directly
  (OpenAI, Anthropic, Bedrock, Vertex, …) you reconfigure the mounted `.pipelex/`
  config — not just an env var. See the Configuration docs.
- **Auth mode** (`AUTH_MODE`) — `none` (default, local), `api_key` (`API_KEY`), or
  `jwt` (`JWT_SECRET_KEY`). The caller is identified by the credential; the runner
  never accepts a user id as a request argument.

### What is NOT in this runner
Durable run storage and by-id polling (`start`/`status`/`result`/`poll`), user-scoped
file storage (`/upload`, `/resolve-storage-url`), org/billing — these are **Pipelex
Platform** features. The open-source runner is execution-only; run a pipeline and get
its output back synchronously via `/pipeline/execute`.
"""
API_TAGS_METADATA: list[dict[str, Any]] = [
    {"name": "health", "description": "Liveness probe (no auth)."},
    {"name": "version", "description": "Pipelex library and API server versions."},
    {"name": "pipeline", "description": "Run pipelines: blocking `execute` and fire-and-callback `start`."},
    {"name": "validate", "description": "Parse, validate, and dry-run an MTHDS bundle."},
    {"name": "build", "description": "Scaffolding helpers: generate inputs, outputs, runner code, concepts, pipe specs."},
    {"name": "agent", "description": "Helpers for AI agents building pipelines (concept/pipe-spec → TOML, model catalog)."},
    {
        "name": "storage",
        "description": (
            "User-scoped file storage. **Pipelex Platform feature** — requires `AUTH_MODE=jwt` "
            "(a per-user identity). Not part of the open-source self-host standard; self-hosters "
            "pass a public URL or base64 data URL directly instead."
        ),
    },
]


def _resolve_api_version() -> str:
    """Best-effort API server version from package metadata; '0.0.0' if unavailable."""
    try:
        return version("pipelex-api")
    except PackageNotFoundError:
        return "0.0.0"


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    Pipelex.make(integration_mode=IntegrationMode.FASTAPI)
    # Process-wide state for the runner-native async run lifecycle (start/poll/result).
    # `run_store` holds run records (in-memory by default); `run_tasks` keeps strong
    # references to in-flight background executions so they aren't garbage-collected.
    app_.state.run_store = make_run_store()
    app_.state.run_tasks = set()
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


app = FastAPI(
    redirect_slashes=False,
    lifespan=lifespan,
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=_resolve_api_version(),
    contact={"name": "Pipelex", "url": "https://docs.pipelex.com/"},
    license_info={"name": "MIT", "url": "https://opensource.org/license/mit"},
    openapi_tags=API_TAGS_METADATA,
    servers=[
        {"url": "http://localhost:8081", "description": "Local self-hosted runner"},
        {"url": "https://api.pipelex.com", "description": "Hosted Pipelex API (runner namespaced under /runner/v1)"},
    ],
)


def _custom_openapi() -> dict[str, Any]:
    """OpenAPI schema with a Bearer security scheme reflecting `AUTH_MODE`.

    Auth is mode-dependent (`none`|`api_key`|`jwt`), so the scheme is declared as an
    OPTIONAL global requirement: it documents `Authorization: Bearer <key|jwt>` for
    the `api_key`/`jwt` modes without falsely implying auth is mandatory (the default
    `none` mode needs no credential). One committed spec stays truthful for all modes.
    """
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=API_TITLE,
        version=_resolve_api_version(),
        description=API_DESCRIPTION,
        routes=app.routes,
        tags=API_TAGS_METADATA,
        contact={"name": "Pipelex", "url": "https://docs.pipelex.com/"},
        license_info={"name": "MIT", "url": "https://opensource.org/license/mit"},
        servers=[
            {"url": "http://localhost:8081", "description": "Local self-hosted runner"},
            {"url": "https://api.pipelex.com", "description": "Hosted Pipelex API (runner namespaced under /runner/v1)"},
        ],
    )
    components = openapi_schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "description": "Bearer token: the `API_KEY` (when `AUTH_MODE=api_key`) or a signed JWT (when `AUTH_MODE=jwt`). Omit when `AUTH_MODE=none`.",
    }
    # Optional requirement: `{}` allows unauthenticated calls (AUTH_MODE=none),
    # `BearerAuth` documents the credential for api_key/jwt modes.
    openapi_schema["security"] = [{}, {"BearerAuth": []}]
    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = _custom_openapi  # type: ignore[method-assign]

cors_origins, cors_allow_credentials = _resolve_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)
app.add_middleware(BaseHTTPMiddleware, dispatch=request_body_size_middleware)

app.include_router(health_router)

# Register all other routes WITH authentication (auto-selects based on AUTH_MODE env var: none/jwt/api_key)
auth_dependency = get_auth_dependency()
app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(auth_dependency)])


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Pipelex API"}
