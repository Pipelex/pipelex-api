import json
from typing import Any

from fastapi import APIRouter
from pipelex.hub import get_library_manager, get_required_pipe, set_current_library
from pipelex.pipeline.validate_bundle import validate_bundle
from pydantic import BaseModel, Field, field_validator

from api.limits import MAX_MTHDS_FILE_BYTES, MAX_MTHDS_FILES_PER_REQUEST, MAX_PIPE_CODE_LEN

router = APIRouter(tags=["build"])


class BuildInputsRequest(BaseModel):
    mthds_contents: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_MTHDS_FILES_PER_REQUEST,
        description="MTHDS contents to load pipes from (always an array, even for single file).",
    )
    pipe_code: str = Field(..., min_length=1, max_length=MAX_PIPE_CODE_LEN, description="Pipe code to generate inputs JSON for.")

    @field_validator("mthds_contents")
    @classmethod
    def _bound_each_file(cls, value: list[str]) -> list[str]:
        for content in value:
            if len(content.encode("utf-8")) > MAX_MTHDS_FILE_BYTES:
                msg = f"MTHDS file exceeds {MAX_MTHDS_FILE_BYTES // 1024} KiB limit"
                raise ValueError(msg)
        return value


@router.post("/build/inputs")
async def build_inputs(request_data: BuildInputsRequest) -> Any:
    """Generate example input JSON for a pipe.

    Pipelex domain failures propagate untouched: the global `PipelexError`
    handler in `api.main` turns them into an RFC 7807 problem response. The
    `try`/`finally` guarantees the library is torn down on both paths.
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
        inputs_json_str = the_pipe.inputs.render_inputs(indent=2)

        return json.loads(inputs_json_str)
    finally:
        if library_id is not None:
            library_manager.teardown(library_id=library_id)
