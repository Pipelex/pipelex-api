from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pipelex.pipeline.validation_report import PipelexValidationReport
from pydantic import Field

from api.routes.pipelex.pipeline import ApiRunner
from api.schemas.models import MthdsContentsRequest

router = APIRouter(tags=["validate"])


class ValidateRequest(MthdsContentsRequest):
    """`/validate` needs nothing beyond the shared `mthds_contents` + `allow_signatures` payload."""


class ValidateResponse(PipelexValidationReport):
    """The canonical `PipelexValidationReport` plus this server's wire-only extras.

    The report fields are inherited — typed models, identical to what the local runtime
    returns for the same bundle. The extras exist for HTTP clients only (the webapp gates
    UI on `success` and reads back `mthds_contents`); they are NOT part of the canonical
    report and no in-process consumer should depend on them.
    """

    mthds_contents: list[str] = Field(..., description="The MTHDS contents that were validated (echo of the request)")
    success: bool = Field(default=True, description="Whether the validation was successful")
    message: str = Field(default="MTHDS content validated successfully", description="Status message")


@router.post("/validate", response_model=ValidateResponse, openapi_extra={"x-mthds-protocol": True})
async def validate_mthds(request_data: ValidateRequest) -> JSONResponse:
    """Validate MTHDS content by parsing, loading, and dry-running pipes (MTHDS Protocol `POST /validate`).

    Thin wrapper over `ApiRunner.validate` — the runner owns backend selection
    (in-process when Temporal is disabled, ONE dispatched worker activity when enabled)
    and always answers with the canonical `PipelexValidationReport`; this route only
    adds the wire extras (`mthds_contents` echo, `success`, `message`).

    Response contract:

    - **Success (200):** the `ValidateResponse` envelope — the canonical report
      (primary `bundle_blueprint`, `pipe_io_contracts` keyed by namespaced `pipe_ref`,
      per-pipe `validated_pipes` sweep outcomes, `pending_signatures` + `is_runnable`
      runnability verdict, best-effort `graph_spec`) plus the wire extras. A bundle
      that declares no `main_pipe` validates fine and carries `graph_spec=null` —
      there is no main-pipe precondition.
    - **Failure (422):** RFC 7807 `application/problem+json` — same shape as every
      other API endpoint. Direct mode: `ValidateBundleError` is a `PipelexError`
      (`error_domain = INPUT`) and propagates to the global handler in
      `api.exception_handlers` unchanged. Temporal mode: the same failure crosses the
      activity boundary as a structured `ErrorReport` and surfaces as
      `WorkflowExecutionError` — also a `PipelexError` — whose `to_error_report()`
      returns the recovered original report (`error_type=ValidateBundleError`,
      `error_domain=input`, caller-facing message), so the handler renders the SAME
      problem document.
    """
    report = await ApiRunner().validate(
        mthds_contents=request_data.mthds_contents,
        allow_signatures=request_data.allow_signatures,
    )
    # Splat the report's own field/value pairs so a future canonical field rides the wire
    # automatically — the wrapper never enumerates (and silently drops) report fields.
    response_data = ValidateResponse.model_validate({**dict(report), "mthds_contents": request_data.mthds_contents})
    return JSONResponse(content=response_data.model_dump(mode="json", serialize_as_any=True, by_alias=True))
