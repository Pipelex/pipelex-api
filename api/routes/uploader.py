import base64
import binascii
import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pipelex import log
from pipelex.hub import get_storage_provider
from pipelex.system.environment import get_optional_env
from pydantic import BaseModel, ConfigDict, Field

from api.error_types import ErrorType
from api.errors import STORAGE_HANDLED_EXCEPTIONS
from api.security import RequestUser, get_request_user

router = APIRouter(tags=["uploader"])

# Hard cap on uploaded files. Base64 encoding inflates by ~4/3, so the JSON
# `data` field is bounded by `MAX_UPLOAD_BYTES * 4 / 3` characters. Enforced
# via Pydantic max_length so oversized payloads are rejected during request
# validation, before the full body is held in memory or decoded.
#
# Operators set MAX_UPLOAD_MIB to raise/lower the limit (default 50 MiB).
# Read once at import time — change requires an API restart.
DEFAULT_MAX_UPLOAD_MIB = 50


def _resolve_max_upload_mib() -> int:
    raw = get_optional_env("MAX_UPLOAD_MIB")
    if not raw:
        return DEFAULT_MAX_UPLOAD_MIB
    try:
        parsed = int(raw)
    except ValueError:
        log.warning(f"Invalid MAX_UPLOAD_MIB={raw!r}, falling back to {DEFAULT_MAX_UPLOAD_MIB}")
        return DEFAULT_MAX_UPLOAD_MIB
    if parsed <= 0:
        log.warning(f"MAX_UPLOAD_MIB must be positive (got {parsed}), falling back to {DEFAULT_MAX_UPLOAD_MIB}")
        return DEFAULT_MAX_UPLOAD_MIB
    return parsed


MAX_UPLOAD_MIB = _resolve_max_upload_mib()
MAX_UPLOAD_BYTES = MAX_UPLOAD_MIB * 1024 * 1024
MAX_UPLOAD_BASE64_CHARS = math.ceil(MAX_UPLOAD_BYTES * 4 / 3) + 4  # +4 for padding slack


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., max_length=512, description="Original filename with extension (e.g. 'resume.pdf')")
    data: str = Field(
        ...,
        max_length=MAX_UPLOAD_BASE64_CHARS,
        description=f"File content as base64-encoded string (max {MAX_UPLOAD_MIB} MiB decoded)",
    )
    content_type: str | None = Field(default=None, max_length=255, description="MIME type (e.g. 'application/pdf')")


class UploadResponse(BaseModel):
    uri: str = Field(..., description="pipelex-storage:// URI for the uploaded file")
    filename: str = Field(..., description="Original filename")


@router.post(
    "/upload",
    summary="Upload a user-scoped file (Pipelex Platform feature)",
    description=(
        "**Pipelex Platform feature — not part of the open-source self-host standard.** "
        "Requires `AUTH_MODE=jwt` (a per-user identity to scope storage under). Self-hosters "
        "should pass a public HTTP(S) URL or a base64 data URL directly as a `Document`/`Image` "
        "input instead of uploading."
    ),
)
async def upload_file(
    body: UploadRequest,
    user: Annotated[RequestUser | None, Depends(get_request_user)],
) -> UploadResponse:
    """Upload a file to pipelex storage, scoped by user.

    Accepts base64-encoded file data, stores it via the configured storage provider,
    and returns the pipelex-storage:// URI that pipelex resolves at runtime.
    """
    if not user or not user.user_id or user.user_id == "anonymous":
        log.warning("upload: unauthenticated request")
        raise HTTPException(
            status_code=401,
            detail={
                "error_type": ErrorType.UNAUTHENTICATED,
                "message": "Authentication required",
            },
        )

    try:
        data = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError) as decode_error:
        log.warning(f"upload: invalid base64 from user={user.user_id} reason={decode_error}")
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": ErrorType.INVALID_BASE64,
                "message": "Request body 'data' is not valid base64",
            },
        ) from decode_error

    if len(data) > MAX_UPLOAD_BYTES:
        log.warning(f"upload: oversized payload from user={user.user_id} size={len(data)}")
        raise HTTPException(
            status_code=413,
            detail={
                "error_type": ErrorType.PAYLOAD_TOO_LARGE,
                "message": f"Decoded file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit",
            },
        )

    ext = body.filename.rsplit(".", 1)[-1] if "." in body.filename else "bin"
    key = f"{user.user_id}/assets/{uuid.uuid4()}.{ext}"

    try:
        storage = get_storage_provider()
        uri = await storage.store(data=data, key=key, content_type=body.content_type)
    except STORAGE_HANDLED_EXCEPTIONS as exc:
        log.error(f"Upload failed for user={user.user_id} key={key}: {exc}")
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": ErrorType.UPLOAD_FAILED,
                "message": "Upload failed. Please try again later.",
            },
        ) from exc

    log.info(f"Uploaded {body.filename} ({len(data)} bytes) → {uri}")

    return UploadResponse(uri=uri, filename=body.filename)
