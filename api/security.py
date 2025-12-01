"""Authentication - Pluggable JWT or API Key validation."""

import os
from typing import Annotated, Any, cast

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pipelex import log

security = HTTPBearer()

# JWT Configuration - Fail fast if JWT is enabled but not configured
JWT_SECRET = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"


async def verify_jwt(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]) -> dict[str, Any]:
    """Validate JWT token from request and return decoded payload.

    The JWT token is extracted from the Authorization header (Bearer token).
    It is verified using the JWT_SECRET_KEY from environment variables.

    Args:
        credentials: The credentials extracted from the Authorization header

    Returns:
        dict: Decoded JWT payload with user info (email, sub, provider, etc.)

    Raises:
        HTTPException: If token is invalid or expired
    """
    if not JWT_SECRET:
        log.error("JWT_SECRET_KEY environment variable is not set")
        msg = "Server configuration error: JWT_SECRET_KEY not configured"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=msg,
        )

    token = credentials.credentials

    try:
        # Decode and verify the JWT token from the request
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
        )

        # Verify required fields
        email = payload.get("email")
        if not email:
            log.warning("JWT missing email field")
            msg = "Invalid token: missing email"
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=msg,
                headers={"WWW-Authenticate": "Bearer"},
            )

        log.info(f"✅ JWT validated for {email}")
        return cast("dict[str, Any]", payload)  # Contains: sub, email, provider, iat, exp

    except jwt.ExpiredSignatureError as exc:
        log.warning("JWT token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        log.warning(f"JWT validation failed: {exc!s}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as exc:
        log.error(f"Unexpected error in JWT verification: {exc!s}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error verifying token: {exc!s}",
        ) from exc


async def verify_api_key(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]) -> str:
    """Validate static API key (local/development mode).

    Args:
        credentials: The credentials extracted from the Authorization header

    Returns:
        str: The validated API key

    Raises:
        HTTPException: If API key is invalid
    """
    try:
        api_key = os.getenv("API_KEY")

        if not api_key:
            log.error("API_KEY environment variable is not set")
            msg = "Server configuration error: API_KEY not configured"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=msg,
            )

        if credentials.credentials != api_key:
            log.warning("API key mismatch")
            msg = "Invalid authentication token"
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=msg,
                headers={"WWW-Authenticate": "Bearer"},
            )

        log.info("✅ API key validated (local mode)")
        return credentials.credentials

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as exc:
        log.error(f"Unexpected error in API key verification: {exc!s}")
        msg = f"Error verifying API key: {exc!s}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=msg,
        ) from exc


# Auto-select authentication method based on environment
def get_auth_dependency():
    """Select authentication dependency based on USE_JWT environment variable.

    Returns:
        The appropriate authentication function (verify_jwt or verify_api_key)
    """
    use_jwt = os.getenv("USE_JWT", "false").lower() == "true"
    return verify_jwt if use_jwt else verify_api_key
