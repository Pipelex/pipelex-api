import json
from typing import Any

from fastapi import APIRouter
from pipelex.core.concepts.concept_representation_generator import ConceptRepresentationFormat
from pipelex.core.pipes.output.output_renderer import render_output
from pipelex.hub import get_library_manager, get_required_pipe, set_current_library
from pipelex.pipeline.validate_bundle import validate_bundle
from pydantic import BaseModel, Field, field_validator

from api.limits import MAX_MTHDS_FILE_BYTES, MAX_MTHDS_FILES_PER_REQUEST, MAX_PIPE_CODE_LEN

router = APIRouter(tags=["build"])


class BuildOutputRequest(BaseModel):
    mthds_contents: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_MTHDS_FILES_PER_REQUEST,
        description="MTHDS contents to load pipes from (always an array, even for single file).",
    )
    pipe_code: str = Field(..., min_length=1, max_length=MAX_PIPE_CODE_LEN, description="Pipe code to generate output JSON for.")
    format: ConceptRepresentationFormat = Field(default=ConceptRepresentationFormat.SCHEMA, description="Format to generate output in.")

    @field_validator("mthds_contents")
    @classmethod
    def _bound_each_file(cls, value: list[str]) -> list[str]:
        for content in value:
            if len(content.encode("utf-8")) > MAX_MTHDS_FILE_BYTES:
                msg = f"MTHDS file exceeds {MAX_MTHDS_FILE_BYTES // 1024} KiB limit"
                raise ValueError(msg)
        return value


@router.post("/build/output")
async def build_output(request_data: BuildOutputRequest) -> Any:
    """Generate example output JSON for a pipe.

    Pipelex domain failures propagate untouched: the global `PipelexError`
    handler in `api.main` turns them into an RFC 7807 problem response. The
    `try`/`finally` guarantees the library is torn down on every path that
    actually opened one — `library_id` stays `None` until `open_library`
    returns, so a failure before that point is a no-op for teardown rather
    than a leak.
    """
    library_manager = get_library_manager()
    library_id: str | None = None

    try:
        validate_bundle_result = await validate_bundle(mthds_contents=request_data.mthds_contents)
        blueprint = validate_bundle_result.blueprints[0]

        library_id, _ = library_manager.open_library()
        set_current_library(library_id)
        library_manager.load_from_blueprints(library_id=library_id, blueprints=[blueprint])

        the_pipe = get_required_pipe(pipe_code=request_data.pipe_code)
        output_json_str = render_output(the_pipe, output_format=request_data.format)

        return json.loads(output_json_str)
    finally:
        if library_id is not None:
            library_manager.teardown(library_id=library_id)
