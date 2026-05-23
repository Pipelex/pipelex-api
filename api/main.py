"""FastAPI app init, middleware, and router registration.

App construction lives here; the failure → HTTP response mapping lives in
`api.exception_handlers` (registered against this app below). Routes
therefore no longer need to catch and shape errors themselves — anything
they raise lands in the right handler by exception class.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pipelex.pipelex import Pipelex
from pipelex.system.environment import get_optional_env
from pipelex.system.runtime import IntegrationMode
from starlette.middleware.base import BaseHTTPMiddleware

from api.disclosure import resolve_disclosure_mode
from api.exception_handlers import register_exception_handlers
from api.middleware import RequestIdMiddleware, request_body_size_middleware
from api.routes import router as api_router
from api.routes.health import router as health_router
from api.security import get_auth_dependency


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    Pipelex.make(integration_mode=IntegrationMode.FASTAPI)
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

fastapi_app = FastAPI(redirect_slashes=False, lifespan=lifespan)

cors_origins, cors_allow_credentials = _resolve_cors_origins()
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)
fastapi_app.add_middleware(BaseHTTPMiddleware, dispatch=request_body_size_middleware)

fastapi_app.include_router(health_router)

# Register all other routes WITH authentication (auto-selects based on AUTH_MODE env var: none/jwt/api_key)
auth_dependency = get_auth_dependency()
fastapi_app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(auth_dependency)])


@fastapi_app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Pipelex API"}


register_exception_handlers(fastapi_app, disclosure_mode=ERROR_DISCLOSURE_MODE)


# RequestIdMiddleware wraps the *entire* FastAPI app — including Starlette's
# ServerErrorMiddleware, which `add_middleware` could only ever nest inside.
# This is what makes it genuinely outermost: the request-id contextvars are
# bound, and `X-Request-ID` is echoed, on every response — the catch-all 500
# included. `app` is the ASGI entrypoint (uvicorn loads `api.main:app`).
app = RequestIdMiddleware(fastapi_app)
