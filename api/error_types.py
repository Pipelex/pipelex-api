"""Centralized `error_type` symbols for API-authored error responses.

Every fixed (non-dynamic) `error_type` an API-authored error emits lives here
so call sites reference symbols, not literals — easier to grep, rename, and
document. The `api.errors` helpers stamp one of these onto the RFC 7807
problem document as the `error_type` extension member.

Pipelex domain errors carry their own open-ended `error_type` (the exception
class name, from the `ErrorReport`) and are NOT enumerated here — they are
deliberately not a fixed set.
"""

from enum import StrEnum


class ErrorType(StrEnum):
    # Authentication / authorization
    UNAUTHENTICATED = "Unauthenticated"
    FORBIDDEN = "Forbidden"
    # A caller asked to run in an orchestration_mode this deployment forbids overriding
    # (per-request override is off — see `allow_request_orchestration_mode_override` in api.toml).
    # A 403: the deployment policy refuses to honor the requested mode.
    ORCHESTRATION_MODE_OVERRIDE_FORBIDDEN = "OrchestrationModeOverrideForbidden"
    INVALID_TOKEN = "InvalidToken"
    TOKEN_EXPIRED = "TokenExpired"
    SERVER_MISCONFIGURED = "ServerMisconfigured"

    # A caller hit `/start` on a deployment whose resolved orchestration mode cannot do genuine
    # async (its orchestrator's `supports_fire_and_forget` is False — e.g. the in-process `direct`
    # base). `/start` is fire-and-forget by nature, so rather than silently running blocking and
    # acking, it refuses HONESTLY with a 400: use `/execute` (synchronous) instead. Checked AFTER
    # the override policy, so a forbidden per-request override still 403s first.
    START_REQUIRES_ASYNC_ORCHESTRATION = "StartRequiresAsyncOrchestration"

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

    # A caller selected a closure by `method_ref` on `/resolve` or `/codegen`. The request envelope
    # accepts the field (it is the registry hinge the spec pins), but no method-registry resolution
    # exists on this server yet — an honest 501, never a silent empty verdict.
    METHOD_REF_NOT_SUPPORTED = "MethodRefNotSupported"

    # Misc
    PACKAGE_NOT_FOUND = "PackageNotFound"
    # The `error_type` for the catch-all 500 emitted by `handle_unexpected_error`
    # (any failure matched by no more-specific handler — not an `ApiError`, a
    # `RequestValidationError`, a `PipelexError`, or an orchestrator plugin's mapped
    # transport exception). Stays in this enum so the same `build_problem_document_from_api_error`
    # builder renders it — same shape as every other API-authored 500.
    INTERNAL_SERVER_ERROR = "InternalServerError"
