"""Authentication module with configurable AUTH_MODE (none, jwt, api_key).

User identity is extracted during auth and stored on request.state.user as a RequestUser.
Route handlers access it via the get_request_user dependency.
"""

import re
from typing import Annotated, Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pipelex import log
from pipelex.system.environment import get_optional_env
from pipelex.types import StrEnum
from pydantic import BaseModel, Field

from api.error_types import ErrorType
from api.errors import raise_internal_server_error, raise_unauthenticated

# JWT Configuration (only used when AUTH_MODE=jwt)
JWT_ALGORITHM = "HS256"

# A caller's `user_id` is the first path segment of every `pipelex-storage://`
# URI and S3 key (`<user_id>/...`). The runner treats it as an OPAQUE id and
# does NOT validate its identity/shape: a self-hosted deployment may use any id
# scheme (uuid, `user_<uuid>`, `google#abc`, an email, …), and a hosted
# deployment behind a trusted proxy receives the *authenticated* id the gateway
# injects (derived from the JWT / API key — never client-chosen). The only
# constraint is PATH-SAFETY, because the id becomes a key segment: it must be a
# single segment that can't enable traversal (no `/`, `\`, NUL/control chars,
# and not `.`/`..`).
_PATH_UNSAFE_CHARS = re.compile(r"[/\\\x00-\x1f]")


def is_safe_user_id(value: str) -> bool:
    """True if `value` is usable as a single, path-safe key segment (opaque id)."""
    return bool(value) and value not in (".", "..") and _PATH_UNSAFE_CHARS.search(value) is None


# `auto_error=False` so a missing/empty/non-Bearer `Authorization` header
# does NOT raise FastAPI's default `HTTPException` (which would emit
# `application/json` `{"detail": "Not authenticated"}` — the old, pre-RFC-7807
# shape) before our verifiers run. With `auto_error=False`, `HTTPBearer`
# returns `None` in that case, and the verifiers below raise
# `raise_unauthenticated(...)` so the response is the same RFC 7807
# `application/problem+json` document as every other 401 on the surface.
security = HTTPBearer(auto_error=False)


class AuthMode(StrEnum):
    NONE = "none"
    JWT = "jwt"
    API_KEY = "api_key"


class ForwardedIdentityHeader(StrEnum):
    """HTTP headers a trusted reverse proxy may forward to authenticate
    a caller when `TRUST_FORWARDED_IDENTITY_HEADERS=true`.

    The runner is a generic execution engine: the ONLY piece of identity
    it consumes is an opaque user id, which it scopes S3 storage keys
    under (`<user_id>/...`). Anything else a proxy might want to forward
    (email, OAuth subject, auth method) is metadata the runner has no
    use for — handlers that need it look it up by `user_id` against the
    deployment's own user store.
    """

    USER_ID = "X-User-Id"


class RequestUser(BaseModel):
    """Authenticated caller identity available to route handlers.

    Holds only `user_id` by design — the runner is a generic execution engine
    and does not own user metadata (email, name, OAuth subject). Deployments
    that need that data look it up by `user_id` against their own user store.
    """

    user_id: str = Field(..., description="Opaque caller identifier supplied by the auth layer or the trusted proxy")


def _set_request_user(request: Request, user_id: str) -> None:
    """Store caller identity on request.state for downstream handlers."""
    request.state.user = RequestUser(user_id=user_id)


def get_auth_mode() -> AuthMode:
    """Read AUTH_MODE from environment. Defaults to 'none'."""
    raw = get_optional_env("AUTH_MODE")
    if not raw:
        return AuthMode.NONE
    try:
        return AuthMode(raw)
    except ValueError:
        log.warning(f"Unknown AUTH_MODE '{raw}', falling back to 'none'")
        return AuthMode.NONE


async def verify_jwt(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict[str, Any]:
    """Validate JWT token from Authorization header using JWT_SECRET_KEY."""
    if credentials is None:
        # Missing, empty, or non-Bearer `Authorization` header. `HTTPBearer`
        # is configured with `auto_error=False` so this branch (rather than
        # FastAPI's default `HTTPException`) shapes the response — same RFC
        # 7807 `application/problem+json` as every other 401.
        log.warning("JWT auth requested without a Bearer token")
        raise_unauthenticated("Missing or malformed Authorization header")

    jwt_secret = get_optional_env("JWT_SECRET_KEY")
    if not jwt_secret:
        log.error("JWT_SECRET_KEY environment variable is not set")
        raise_internal_server_error("Server configuration error: JWT_SECRET_KEY not configured", error_type=ErrorType.SERVER_MISCONFIGURED)

    token = credentials.credentials

    try:
        payload = jwt.decode(  # type: ignore[reportUnknownMemberType]
            token,
            jwt_secret,
            algorithms=[JWT_ALGORITHM],
        )

        # The caller identifier MUST be supplied as an explicit `user_id`
        # claim. We deliberately do NOT fall back to the standard `sub`
        # claim. The id is opaque (any scheme is fine — see `is_safe_user_id`);
        # we only reject values that aren't a single path-safe segment, since
        # the id becomes the owner segment of every storage key.
        user_id = payload.get("user_id")
        if not user_id:
            log.warning("JWT missing user_id claim")
            raise_unauthenticated("Invalid token: missing user_id claim", error_type=ErrorType.INVALID_TOKEN)
        if not isinstance(user_id, str) or not is_safe_user_id(user_id):
            log.warning(f"JWT user_id claim is not a path-safe segment: {user_id!r}")
            raise_unauthenticated("Invalid token: user_id claim must be a single path-safe segment", error_type=ErrorType.INVALID_TOKEN)
        _set_request_user(request, user_id=user_id)

        return payload

    except jwt.ExpiredSignatureError:
        log.warning("JWT token has expired")
        raise_unauthenticated("Token expired", error_type=ErrorType.TOKEN_EXPIRED)
    except jwt.InvalidTokenError as exc:
        log.warning(f"JWT validation failed: {exc!s}")
        raise_unauthenticated("Invalid token", error_type=ErrorType.INVALID_TOKEN)


async def verify_api_key(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]) -> str:
    """Validate static API key from Authorization header against API_KEY env var.

    No user identity is available with static API keys — this is a shared developer key.
    """
    if credentials is None:
        # Missing, empty, or non-Bearer `Authorization` header. See the
        # matching branch in `verify_jwt` for why this lives here and not
        # in `HTTPBearer`'s default `auto_error=True` behavior.
        log.warning("API key auth requested without a Bearer token")
        raise_unauthenticated("Missing or malformed Authorization header")

    api_key = get_optional_env("API_KEY")

    if not api_key:
        log.error("API_KEY environment variable is not set")
        raise_internal_server_error("Server configuration error: API_KEY not configured", error_type=ErrorType.SERVER_MISCONFIGURED)

    if credentials.credentials != api_key:
        log.warning("API key mismatch")
        raise_unauthenticated("Invalid authentication token", error_type=ErrorType.INVALID_TOKEN)

    return credentials.credentials


async def no_auth(request: Request) -> None:
    """No-op auth dependency for AUTH_MODE=none.

    By default, requests stay anonymous. If the API sits behind a trusted
    reverse proxy / API gateway that authenticates callers and forwards the
    caller identifier via the `X-User-Id` header, set
    TRUST_FORWARDED_IDENTITY_HEADERS=true to read it. That single header is
    the only thing the runner trusts from a forwarded request — any other
    `X-User-*` headers a caller might attach are ignored.

    The header is NOT trusted unless that env var is set, because any
    external client could otherwise forge it when the API is reachable
    directly. With the flag enabled, you are responsible for ensuring your
    proxy strips `X-User-Id` from inbound requests before adding its own.
    """
    if get_optional_env("TRUST_FORWARDED_IDENTITY_HEADERS") != "true":
        return

    user_id = request.headers.get(ForwardedIdentityHeader.USER_ID)
    if not user_id or user_id == "anonymous":
        return
    if not is_safe_user_id(user_id):
        log.warning(f"Forwarded X-User-Id is not a path-safe segment, ignoring: {user_id!r}")
        return

    _set_request_user(request, user_id=user_id)


async def get_request_user(request: Request) -> RequestUser | None:
    """Dependency to retrieve the authenticated user identity.

    Returns the RequestUser if identity was established during auth, or None
    if no identity is available (e.g. static API key, or no auth).

    Usage in route handlers:
        async def my_endpoint(user: Annotated[RequestUser | None, Depends(get_request_user)]):
            if user:
                log.info(f"Request from {user.user_id}")
    """
    return getattr(request.state, "user", None)


def get_auth_dependency() -> Any:
    """Select authentication dependency based on AUTH_MODE env var.

    - none: No authentication (open source default, or behind API Gateway)
    - jwt: Validate JWT tokens
    - api_key: Validate static API key
    """
    auth_mode = get_auth_mode()
    match auth_mode:
        case AuthMode.NONE:
            return no_auth
        case AuthMode.JWT:
            return verify_jwt
        case AuthMode.API_KEY:
            return verify_api_key
