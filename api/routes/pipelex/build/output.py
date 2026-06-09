import json
from typing import Any

from fastapi import APIRouter
from pipelex.core.concepts.concept_representation_generator import ConceptRepresentationFormat
from pipelex.core.pipes.output.output_renderer import render_output
from pipelex.hub import clear_current_library, get_current_library_id_or_none, get_library_manager, get_required_pipe
from pipelex.pipeline.validate_bundle import validate_bundle
from pydantic import Field

from api.limits import MAX_PIPE_CODE_LEN
from api.schemas.models import MthdsContentsRequest

router = APIRouter(tags=["build"])


class BuildOutputRequest(MthdsContentsRequest):
    pipe_code: str = Field(..., min_length=1, max_length=MAX_PIPE_CODE_LEN, description="Pipe code to generate output JSON for.")
    format: ConceptRepresentationFormat = Field(default=ConceptRepresentationFormat.SCHEMA, description="Format to generate output in.")


@router.post("/build/output", summary="Generate a pipe's output representation (schema, JSON, or Python)")
async def build_output(request_data: BuildOutputRequest) -> Any:
    """Generate example output JSON for a pipe.

    `validate_bundle` opens a single library, loads the bundle, and on success leaves it loaded +
    current (the D6 loaded-on-success contract); on failure it tears that library down itself. So
    this route reuses the already-current library directly — no second `open_library` /
    `load_from_blueprints` — and owns the teardown only on the success path it reaches. Scoping the
    sweep to the requested pipe (`dry_run_pipe_codes`) avoids dry-running unrelated sibling pipes.

    Pipelex domain failures propagate untouched: the global `PipelexError` handler in
    `api.exception_handlers` turns them into an RFC 7807 problem response.
    """
    library_manager = get_library_manager()

    # If this raises, validate_bundle has already torn down its own library — nothing to clean up here.
    await validate_bundle(
        mthds_contents=request_data.mthds_contents,
        allow_signatures=request_data.allow_signatures,
        dry_run_pipe_codes=[request_data.pipe_code],
    )

    # Success: validate_bundle left its library loaded + current. Read the pipe from it, then own its teardown.
    library_id = get_current_library_id_or_none()
    try:
        the_pipe = get_required_pipe(pipe_code=request_data.pipe_code)
        output_json_str = render_output(the_pipe, output_format=request_data.format)
        return json.loads(output_json_str)
    finally:
        clear_current_library()
        if library_id is not None:
            library_manager.teardown(library_id=library_id)
