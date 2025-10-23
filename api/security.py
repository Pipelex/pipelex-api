import os
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pipelex import log

security = HTTPBearer()


async def verify_token(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]) -> str:
    """Verify the Bearer token against an API key from secret provider.

    Args:
        credentials: The credentials extracted from the Authorization header

    Returns:
        str: The verified token

    Raises:
        HTTPException: If the token is invalid
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
            log.warning("Token mismatch")
            msg = "Invalid authentication token"
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=msg,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return credentials.credentials
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as exc:
        log.error(f"Unexpected error in token verification: {exc!s}")
        msg = f"Error verifying token: {exc!s}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=msg,
        ) from exc
