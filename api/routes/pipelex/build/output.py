import json
from typing import Annotated, Any, Literal, Union

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pipelex.core.concepts.concept_representation_generator import ConceptRepresentationFormat
from pipelex.core.pipes.output.output_renderer import render_output
from pipelex.hub import clear_current_library, get_current_library_id_or_none, get_library_manager, get_required_pipe
from pipelex.pipeline.exceptions import ValidateBundleError
from pipelex.pipeline.validate_bundle import validate_bundle
from pydantic import BaseModel, Field

from api.limits import MAX_PIPE_CODE_LEN
from api.routes.pipelex.crate_ops import CrateInvalidReport, invalid_crate_report_response
from api.schemas.models import MthdsContentsRequest

router = APIRouter(tags=["build"])


class BuildOutputRequest(MthdsContentsRequest):
    pipe_code: str = Field(..., min_length=1, max_length=MAX_PIPE_CODE_LEN, description="Pipe code to generate output JSON for.")
    format: ConceptRepresentationFormat = Field(default=ConceptRepresentationFormat.SCHEMA, description="Format to generate output in.")


class BuildOutputValidReport(BaseModel):
    """The 200 **valid** arm: the example output representation for the requested pipe."""

    is_valid: Literal[True] = True
    pipe_code: str = Field(..., description="The pipe the output representation was generated for (echo of the request).")
    format: ConceptRepresentationFormat = Field(..., description="The representation format (echo of the request).")
    output: dict[str, Any] = Field(..., description="Example output JSON for the pipe (fed by the pipe's IO contract).")
    message: str = Field(default="Output representation generated successfully", description="Status message")


# Discriminated 200 response union: the `/validate` discipline — the verdict rides `is_valid`,
# never the HTTP status (breaking change from the previous bare-JSON body).
BuildOutputResponse = Annotated[Union[BuildOutputValidReport, CrateInvalidReport], Field(discriminator="is_valid")]


@router.post("/build/output", response_model=BuildOutputResponse)
async def build_output(request_data: BuildOutputRequest) -> JSONResponse:
    """Generate example output JSON for a pipe (the output projection, per pipe).

    `validate_bundle` opens a single library, loads the bundle, and on success leaves it loaded +
    current (the D6 loaded-on-success contract); on failure it tears that library down itself. So
    this route reuses the already-current library directly — no second `open_library` /
    `load_from_blueprints` — and owns the teardown only on the success path it reaches. Scoping the
    sweep to the requested pipe (`dry_run_pipe_codes`) avoids dry-running unrelated sibling pipes.

    Response contract (the `/validate` discipline): an invalid bundle is a produced verdict —
    a **200** `is_valid: false` with the structured `validation_errors[]` — not a 422. Non-2xx is
    reserved for no-verdict conditions (request shape, auth, server fault), rendered RFC 7807 by
    the global handlers.
    """
    library_manager = get_library_manager()

    try:
        # If this raises, validate_bundle has already torn down its own library — nothing to clean up here.
        await validate_bundle(
            mthds_contents=request_data.mthds_contents,
            allow_signatures=request_data.allow_signatures,
            dry_run_pipe_codes=[request_data.pipe_code],
        )
    except ValidateBundleError as validate_error:
        return invalid_crate_report_response(validate_error.to_error_report())

    # Success: validate_bundle left its library loaded + current. Read the pipe from it, then own its teardown.
    library_id = get_current_library_id_or_none()
    try:
        the_pipe = get_required_pipe(pipe_code=request_data.pipe_code)
        output_json_str = render_output(the_pipe, output_format=request_data.format)
        report = BuildOutputValidReport(
            pipe_code=request_data.pipe_code,
            format=request_data.format,
            output=json.loads(output_json_str),
        )
        return JSONResponse(content=report.model_dump(mode="json", by_alias=True))
    finally:
        clear_current_library()
        if library_id is not None:
            library_manager.teardown(library_id=library_id)
