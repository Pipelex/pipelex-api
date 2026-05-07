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


class RequestUser(BaseModel):
    """Authenticated user identity, populated during auth and available to route handlers."""

    email: str = Field(..., description="User's email address")
    sub: str = Field(..., description="User's composite key (e.g. 'google#123')")
    user_id: str = Field(default="anonymous", description="User's UUID from the users table")
    auth_method: str = Field(..., description="How the user authenticated: 'jwt', 'api_key', or 'gateway'")


def _set_request_user(request: Request, email: str, sub: str, auth_method: str, user_id: str = "anonymous") -> None:
    """Store user identity on request.state for downstream handlers."""
    request.state.user = RequestUser(email=email, sub=sub, user_id=user_id, auth_method=auth_method)


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

        email = payload.get("email")
        if not email:
            log.warning("JWT missing email field")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error_type": ErrorType.INVALID_TOKEN, "message": "Invalid token: missing email"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        sub = payload.get("sub", "")
        user_id = payload.get("user_id", "anonymous")
        _set_request_user(request, email=email, sub=sub, user_id=user_id, auth_method="jwt")

        return payload  # Contains: sub, user_id, email, provider, iat, exp

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

    By default, requests stay anonymous. If you sit this API behind a trusted
    reverse proxy / API gateway that authenticates users and forwards
    identity via X-User-Email / X-User-Sub / X-User-Id / X-Auth-Method
    headers, set TRUST_FORWARDED_IDENTITY_HEADERS=true to read them.

    The headers are NOT trusted unless that env var is set, because any
    external client could otherwise forge them when the API is reachable
    directly. With the flag enabled, you are responsible for ensuring your
    proxy strips these headers from inbound requests before adding its own.
    """
    if get_optional_env("TRUST_FORWARDED_IDENTITY_HEADERS") != "true":
        return

    email = request.headers.get("x-user-email")
    sub = request.headers.get("x-user-sub")
    user_id = request.headers.get("x-user-id", "anonymous")
    auth_method = request.headers.get("x-auth-method")

    if email and sub:
        _set_request_user(request, email=email, sub=sub, user_id=user_id, auth_method=auth_method or "gateway")


async def get_request_user(request: Request) -> RequestUser | None:
    """Dependency to retrieve the authenticated user identity.

    Returns the RequestUser if identity was established during auth, or None
    if no identity is available (e.g. static API key, or no auth).

    Usage in route handlers:
        async def my_endpoint(user: Annotated[RequestUser | None, Depends(get_request_user)]):
            if user:
                log.info(f"Request from {user.email}")
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
