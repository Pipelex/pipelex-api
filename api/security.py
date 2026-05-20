"""Authentication module with configurable AUTH_MODE (none, jwt, api_key).

User identity is extracted during auth and stored on request.state.user as a RequestUser.
Route handlers access it via the get_request_user dependency.
"""

from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pipelex import log
from pipelex.system.environment import get_optional_env
from pipelex.types import StrEnum
from pydantic import BaseModel, Field

from api.error_types import ErrorType

# JWT Configuration (only used when AUTH_MODE=jwt)
JWT_ALGORITHM = "HS256"

security = HTTPBearer()


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
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> dict[str, Any]:
    """Validate JWT token from Authorization header using JWT_SECRET_KEY."""
    jwt_secret = get_optional_env("JWT_SECRET_KEY")
    if not jwt_secret:
        log.error("JWT_SECRET_KEY environment variable is not set")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_type": ErrorType.SERVER_MISCONFIGURED, "message": "Server configuration error: JWT_SECRET_KEY not configured"},
        )

    token = credentials.credentials

    try:
        payload = jwt.decode(  # type: ignore[reportUnknownMemberType]
            token,
            jwt_secret,
            algorithms=[JWT_ALGORITHM],
        )

        # The caller identifier MUST be supplied as an explicit `user_id`
        # claim. We deliberately do NOT fall back to the standard `sub`
        # claim: storage URIs require the owner segment to be a UUID
        # (`parse_storage_uri` in `routes/storage.py`), and provider-issued
        # `sub` values like `"google#abc"` would let a caller upload to
        # S3 under a key that `/resolve-storage-url` would later refuse to
        # resolve. Deployments using OAuth JWTs must mint their own
        # `user_id` claim (a UUID for that caller) when issuing tokens.
        user_id = payload.get("user_id")
        if not user_id:
            log.warning("JWT missing user_id claim")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_type": ErrorType.INVALID_TOKEN, "message": "Invalid token: missing user_id claim"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        _set_request_user(request, user_id=user_id)

        return payload

    except jwt.ExpiredSignatureError as exc:
        log.warning("JWT token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_type": ErrorType.TOKEN_EXPIRED, "message": "Token expired"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        log.warning(f"JWT validation failed: {exc!s}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_type": ErrorType.INVALID_TOKEN, "message": "Invalid token"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def verify_api_key(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]) -> str:
    """Validate static API key from Authorization header against API_KEY env var.

    No user identity is available with static API keys — this is a shared developer key.
    """
    api_key = get_optional_env("API_KEY")

    if not api_key:
        log.error("API_KEY environment variable is not set")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_type": ErrorType.SERVER_MISCONFIGURED, "message": "Server configuration error: API_KEY not configured"},
        )

    if credentials.credentials != api_key:
        log.warning("API key mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_type": ErrorType.INVALID_TOKEN, "message": "Invalid authentication token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

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
