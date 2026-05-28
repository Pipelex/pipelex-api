"""Centralized `error_type` symbols for API-authored error responses.

Every fixed (non-dynamic) `error_type` an API-authored error emits lives here
so call sites reference symbols, not literals — easier to grep, rename, and
document. The `api.errors` helpers stamp one of these onto the RFC 7807
problem document as the `error_type` extension member.

Pipelex domain errors carry their own open-ended `error_type` (the exception
class name, from the `ErrorReport`) and are NOT enumerated here — they are
deliberately not a fixed set.
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
    # The `error_type` for the catch-all 500 emitted by `handle_unexpected_error`
    # (any failure that is neither a `PipelexError`, a `TemporalError`, nor an
    # `ApiError`). Stays in this enum so the same `build_problem_document_from_api_error`
    # builder renders it — same shape as every other API-authored 500.
    INTERNAL_SERVER_ERROR = "InternalServerError"
