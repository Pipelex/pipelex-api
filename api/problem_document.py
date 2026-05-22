"""RFC 7807 problem-document construction.

A pure module — no FastAPI imports, no I/O. It turns the two error sources of
the API into the single `application/problem+json` body shape clients see:

- `build_problem_document` — for a pipelex `ErrorReport`. Delegates to
  `ErrorReport.to_problem_document(...)`: pipelex owns the field mapping
  (standard RFC 7807 slots plus classification extension members) and the
  `disclosure_mode` redaction. The API only chooses the disclosure mode and
  supplies the request context.
- `build_problem_document_from_api_error` — for an API-authored 4xx that has
  no `ErrorReport` behind it (`raise_validation_error` and friends in
  `api.errors`). Built from the static `ErrorType` enum; always `INPUT` domain.

Both return a plain `dict` the caller serializes as `application/problem+json`.
"""

from typing import Any

from pipelex.base_exceptions import DisclosureMode, ErrorDomain, ErrorReport

from api.error_types import ErrorType
from api.error_uri import error_type_title, error_type_uri


def build_problem_document(
    report: ErrorReport,
    *,
    instance: str | None,
    request_id: str | None,
    disclosure_mode: DisclosureMode,
) -> dict[str, Any]:
    """Build an RFC 7807 problem document from a pipelex `ErrorReport`.

    Thin wrapper over `ErrorReport.to_problem_document(...)`: the field mapping
    and the `disclosure_mode` redaction both live upstream in pipelex. The
    API's only responsibility is to choose the disclosure mode and pass the
    request context through.
    """
    return report.to_problem_document(
        instance=instance,
        request_id=request_id,
        disclosure_mode=disclosure_mode,
    )


def build_problem_document_from_api_error(
    error_type: ErrorType,
    message: str,
    status: int,
    *,
    instance: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    """Build an RFC 7807 problem document for an API-authored error.

    Used by the `api.errors` 4xx helpers, whose failures are the API's own
    request validation rather than a pipelex domain error — there is no
    `ErrorReport` to delegate to. The `type` URI and `title` are derived from
    the static `ErrorType` enum the same way pipelex derives them for its own
    classes, so an API-authored error is shape-identical to a pipelex one on
    the wire. Always `INPUT` domain — these are errors the caller can fix.

    `None`-valued context (`instance`, `request_id`) is dropped rather than
    emitted as `null`, matching `ErrorReport.to_problem_document` semantics.
    """
    document: dict[str, Any] = {
        "type": error_type_uri(error_type),
        "title": error_type_title(error_type),
        "status": status,
        "detail": message,
    }
    if instance is not None:
        document["instance"] = instance
    if request_id is not None:
        document["request_id"] = request_id
    document["error_type"] = error_type
    document["error_domain"] = ErrorDomain.INPUT
    return document
