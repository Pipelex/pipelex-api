from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pipelex.builder.runner_code import generate_runner_code
from pipelex.core.interpreter.interpreter import PipelexInterpreter
from pipelex.hub import get_library_manager, get_required_pipe, set_current_library
from pipelex.pipeline.bundle_validator import BundleValidator
from pydantic import BaseModel, Field, field_validator

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
    allow_signatures: bool = Field(
        default=False,
        description="When true, the validation sweep tolerates unimplemented pipe signatures instead of rejecting the "
        "bundle (signatures dry-run trivially by minting a mock). Defaults to false (strict).",
    )

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
    """Generate Python runner code for a pipe from MTHDS content.

    Pipelex domain failures propagate untouched: the global `PipelexError`
    handler in `api.exception_handlers` turns them into an RFC 7807 problem response. The
    `try`/`finally` guarantees the library is torn down on every path that
    actually opened one — `library_id` stays `None` until `open_library`
    returns, so a failure before that point is a no-op for teardown rather
    than a leak. Matches the pattern in `build/inputs.py` and `build/output.py`.
    """
    library_manager = get_library_manager()
    library_id: str | None = None

    try:
        library_id, _ = library_manager.open_library()
        set_current_library(library_id)

        converter = PipelexInterpreter()
        blueprints = [converter.make_pipelex_bundle_blueprint(mthds_content=content) for content in request_data.mthds_contents]
        pipes = library_manager.load_from_blueprints(library_id=library_id, blueprints=blueprints)

        # Public inner sweep against the library we just opened: it runs the static
        # `validate_with_libraries` wiring check, the signature pre-pass (strict unless the request
        # opts in via `allow_signatures`), and the per-pipe dry run, raising on any unexpected
        # failure — and crucially never tears the library down, so it stays loaded + current for
        # `generate_runner_code` below.
        await BundleValidator().validate_pipes(pipes=pipes, library_id=library_id, allow_signatures=request_data.allow_signatures)

        the_pipe = get_required_pipe(request_data.pipe_code)
        python_code = generate_runner_code(pipe=the_pipe)

        response_data = BuildRunnerResponse(
            python_code=python_code,
            pipe_code=request_data.pipe_code,
            success=True,
            message="Runner code generated successfully",
        )
        return JSONResponse(content=response_data.model_dump(serialize_as_any=True))
    finally:
        if library_id is not None:
            library_manager.teardown(library_id=library_id)
