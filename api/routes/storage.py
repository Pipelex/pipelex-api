"""Resolve pipelex-storage:// URIs to short-lived presigned URLs.

A single endpoint handles every pipelex-storage URI regardless of shape
(user uploads, pipeline outputs, etc.). The invariant: the first path
segment after the scheme must be a UUID that equals the requester's
user_id. Everything downstream is a trusted S3 key.
"""

import mimetypes
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import parse_qs, urlparse

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends
from pipelex import log
from pipelex.hub import get_storage_provider
from pipelex.tools.storage.storage_provider_abstract import PIPELEX_STORAGE_SCHEME
from pydantic import BaseModel, ConfigDict, Field

from api.error_types import ErrorType
from api.errors import raise_bad_request, raise_forbidden, raise_internal_server_error, raise_unauthenticated
from api.security import USER_ID_UUID_REGEX, RequestUser, get_request_user

router = APIRouter(tags=["storage"])

_EXTENSION_REGEX = re.compile(r"^[a-zA-Z0-9]+$")
_FALLBACK_LIFESPAN_SECONDS = 900


class ResolveStorageUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., max_length=512, description="pipelex-storage:// URI to resolve")


class ResolveStorageUrlResponse(BaseModel):
    url: str = Field(..., description="Short-lived presigned URL, fetchable directly by the browser")
    expires_at: datetime = Field(..., description="UTC expiry timestamp of the presigned URL")
    content_type: str | None = Field(default=None, description="Content type guessed from the URI extension")


def parse_storage_uri(uri: str) -> tuple[str, str]:
    """Parse a pipelex-storage:// URI.

    Returns:
        Tuple of (user_id, extension).

    Raises:
        ValueError: if the URI is malformed, contains traversal segments, or has no extension.
    """
    if not uri.startswith(PIPELEX_STORAGE_SCHEME):
        msg = "URI must start with pipelex-storage://"
        raise ValueError(msg)

    path = uri.removeprefix(PIPELEX_STORAGE_SCHEME)

    if not path or path.startswith("/") or path.endswith("/"):
        msg = "URI path must not be empty and must not start or end with '/'"
        raise ValueError(msg)

    segments = path.split("/")
    if any(seg in ("", "..", ".") for seg in segments):
        msg = "URI path contains invalid segments"
        raise ValueError(msg)

    if len(segments) < 2:
        msg = "URI must have at least {user_id}/{filename}"
        raise ValueError(msg)

    user_id = segments[0]
    if not USER_ID_UUID_REGEX.match(user_id):
        msg = "URI first segment must be a UUID user_id"
        raise ValueError(msg)

    last_segment = segments[-1]
    if "." not in last_segment or last_segment.startswith(".") or last_segment.endswith("."):
        msg = "URI must end with a filename that includes an extension"
        raise ValueError(msg)

    extension = last_segment.rsplit(".", 1)[-1]
    if not _EXTENSION_REGEX.match(extension):
        msg = "URI extension must be alphanumeric"
        raise ValueError(msg)

    return user_id, extension


def is_presigned(url: str) -> bool:
    """A presigned S3 URL carries X-Amz-Signature (and X-Amz-Expires) in the query string."""
    return "X-Amz-Signature=" in url


def expires_at_from_presigned(presigned_url: str) -> datetime:
    """Derive expiry from X-Amz-Date + X-Amz-Expires. No drift from config.

    Falls back to now + _FALLBACK_LIFESPAN_SECONDS if the URL is missing the
    expected params (shouldn't happen after is_presigned check, but keeps the
    function total).
    """
    parsed = urlparse(presigned_url)
    query = parse_qs(parsed.query)

    amz_date_values = query.get("X-Amz-Date")
    amz_expires_values = query.get("X-Amz-Expires")

    now = datetime.now(UTC)
    if not amz_date_values or not amz_expires_values:
        return now + timedelta(seconds=_FALLBACK_LIFESPAN_SECONDS)

    try:
        signed_at = datetime.strptime(amz_date_values[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        expires_seconds = int(amz_expires_values[0])
    except (ValueError, TypeError):
        return now + timedelta(seconds=_FALLBACK_LIFESPAN_SECONDS)

    return signed_at + timedelta(seconds=expires_seconds)


@router.post("/resolve-storage-url")
async def resolve_storage_url(
    body: ResolveStorageUrlRequest,
    user: Annotated[RequestUser | None, Depends(get_request_user)],
) -> ResolveStorageUrlResponse:
    """Resolve a pipelex-storage:// URI to a short-lived presigned URL.

    Security:
      1. Requires authenticated user (user_id != "anonymous").
      2. URI format is validated to block path traversal and malformed segments.
      3. The URI's first path segment must equal the requester's user_id (ownership check).
      4. Fails loud if the storage provider falls back to a non-presigned URL.
    """
    if not user or not user.user_id or user.user_id == "anonymous":
        log.warning("resolve-storage-url: unauthenticated request")
        raise_unauthenticated("Authentication required")

    try:
        owner_user_id, extension = parse_storage_uri(body.uri)
    except ValueError as validation_error:
        log.warning(f"resolve-storage-url: invalid URI from user={user.user_id} reason={validation_error}")
        raise_bad_request(str(validation_error), error_type=ErrorType.INVALID_URI)

    if owner_user_id != user.user_id:
        log.warning(f"resolve-storage-url: ownership mismatch jwt_user={user.user_id} uri_user={owner_user_id}")
        raise_forbidden("You do not own this file")

    # Most backend failures surface as a pipelex `StorageError` (a `PipelexError`)
    # and propagate to the global handler. The narrow catch below covers the
    # documented upstream wrapping gaps (pipelex-changes.md Stage 7 #12/#13):
    # `LocalStorageProvider.public_url` can leak raw `OSError` on permission
    # errors, and S3's presign path has historically leaked `BotoCoreError` /
    # `ClientError` on credential-retrieval or endpoint-resolution failures.
    # Without this catch those escape to the generic 500 handler and the response
    # loses its presign classification (`PresignFailed` → `InternalServerError`).
    # The non-presigned fallback below is an API-layer configuration check, not
    # a backend error, so the API authors that 500 itself.
    storage = get_storage_provider()
    try:
        url = await storage.public_url(body.uri)
    except (OSError, BotoCoreError, ClientError):
        raise_internal_server_error("Storage backend failure while resolving URL", error_type=ErrorType.PRESIGN_FAILED)

    if not url or not is_presigned(url):
        log.error(f"resolve-storage-url: storage returned non-presigned URL (signed_urls_lifespan_seconds disabled or fallback). user={user.user_id}")
        raise_internal_server_error(
            "Storage provider is not configured for presigned URLs",
            error_type=ErrorType.PRESIGN_FAILED,
        )

    expires_at = expires_at_from_presigned(url)
    content_type, _ = mimetypes.guess_type(f"file.{extension}")

    uri_tail = body.uri.rsplit("/", 1)[-1]
    log.info(f"resolve-storage-url: ok user={user.user_id} uri_tail={uri_tail} expires_at={expires_at.isoformat()}")

    return ResolveStorageUrlResponse(
        url=url,
        expires_at=expires_at,
        content_type=content_type,
    )
