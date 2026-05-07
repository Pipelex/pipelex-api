from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pipelex.builder.runner_code import generate_runner_code
from pipelex.core.interpreter.interpreter import PipelexInterpreter
from pipelex.hub import get_library_manager, get_required_pipe, set_current_library
from pipelex.pipe_run.dry_run import dry_run_pipes
from pydantic import BaseModel, Field, field_validator

from api.errors import ENDPOINT_HANDLED_EXCEPTIONS, raise_internal_error
from api.limits import MAX_MTHDS_FILE_BYTES, MAX_MTHDS_FILES_PER_REQUEST, MAX_PIPE_CODE_LEN

router = APIRouter(tags=["build"])


class BuildRunnerRequest(BaseModel):
    mthds_contents: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_MTHDS_FILES_PER_REQUEST,
        description="MTHDS contents to load and generate runner code for (always an array, even for single file).",
    )
    pipe_code: str = Field(..., min_length=1, max_length=MAX_PIPE_CODE_LEN, description="Pipe code to generate runner code for.")

    @field_validator("mthds_contents")
    @classmethod
    def _bound_each_file(cls, value: list[str]) -> list[str]:
        for content in value:
            if len(content.encode("utf-8")) > MAX_MTHDS_FILE_BYTES:
                msg = f"MTHDS file exceeds {MAX_MTHDS_FILE_BYTES // 1024} KiB limit"
                raise ValueError(msg)
        return value


class BuildRunnerResponse(BaseModel):
    python_code: str = Field(..., description="Generated Python code for running the workflow")
    pipe_code: str = Field(..., description="Pipe code that was used")
    success: bool = Field(default=True, description="Whether the operation was successful")
    message: str = Field(default="Runner code generated successfully", description="Status message")


@router.post("/build/runner", response_model=BuildRunnerResponse)
async def build_runner(request_data: BuildRunnerRequest) -> JSONResponse:
    """Generate Python runner code for a pipe from MTHDS content."""
    library_manager = get_library_manager()
    library_id, _ = library_manager.open_library()
    set_current_library(library_id)

    try:
        converter = PipelexInterpreter()
        blueprints = [converter.make_pipelex_bundle_blueprint(mthds_content=content) for content in request_data.mthds_contents]
        pipes = library_manager.load_from_blueprints(library_id=library_id, blueprints=blueprints)

        for pipe in pipes:
            pipe.validate_with_libraries()
            await dry_run_pipes(pipes=[pipe], raise_on_failure=True)

        the_pipe = get_required_pipe(request_data.pipe_code)
        python_code = generate_runner_code(pipe=the_pipe)

        response_data = BuildRunnerResponse(
            python_code=python_code,
            pipe_code=request_data.pipe_code,
            success=True,
            message="Runner code generated successfully",
        )
        return JSONResponse(content=response_data.model_dump(serialize_as_any=True))

    except ENDPOINT_HANDLED_EXCEPTIONS as exc:
        raise_internal_error(exc, context=f"build_runner for pipe '{request_data.pipe_code}'")
    finally:
        library_manager.teardown(library_id=library_id)
