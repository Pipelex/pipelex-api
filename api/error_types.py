"""Centralized error_type symbols emitted in `{"detail": {"error_type": ...}}`.

Every fixed (non-dynamic) `error_type` string returned by an endpoint lives
here so call sites reference symbols, not literals — easier to grep, rename,
and document. Dynamic error types (e.g., the exception class name produced by
`raise_internal_error`) are NOT enumerated; they reflect runtime exception
types and are deliberately open-ended.
"""

from pipelex.types import StrEnum


class ErrorType(StrEnum):
    # Authentication / authorization
    UNAUTHENTICATED = "Unauthenticated"
    FORBIDDEN = "Forbidden"
    INVALID_TOKEN = "InvalidToken"
    TOKEN_EXPIRED = "TokenExpired"
    SERVER_MISCONFIGURED = "ServerMisconfigured"

    # Request validation
    BAD_REQUEST = "BadRequest"
    VALIDATION_ERROR = "ValidationError"
    INVALID_JSON = "InvalidJSON"
    INVALID_CALLBACK_URLS = "InvalidCallbackUrls"
    INVALID_MODEL_CATEGORY = "InvalidModelCategory"
    INVALID_BASE64 = "InvalidBase64"
    INVALID_URI = "InvalidUri"
    PAYLOAD_TOO_LARGE = "PayloadTooLarge"

    # Storage / upload
    UPLOAD_FAILED = "UploadFailed"
    PRESIGN_FAILED = "PresignFailed"

    # Misc
    PACKAGE_NOT_FOUND = "PackageNotFound"
