from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pipelex.builder.runner_code import generate_runner_code
from pipelex.core.interpreter.interpreter import PipelexInterpreter
from pipelex.hub import get_library_manager, get_required_pipe, set_current_library
from pipelex.pipe_run.exceptions import DryRunError
from pipelex.pipeline.bundle_validator import BundleValidator, DryRunOutput, DryRunStatus
from pipelex.pipeline.exceptions import ValidateBundleError
from pydantic import BaseModel, Field

from api.errors import raise_validation_error
from api.limits import MAX_PIPE_CODE_LEN
from api.schemas.models import MthdsContentsRequest

router = APIRouter(tags=["build"])


class BuildRunnerRequest(MthdsContentsRequest):
    pipe_code: str = Field(..., min_length=1, max_length=MAX_PIPE_CODE_LEN, description="Pipe code to generate runner code for.")


class BuildRunnerResponse(BaseModel):
    python_code: str = Field(..., description="Generated Python code for running the workflow")
    pipe_code: str = Field(..., description="Pipe code that was used")
    success: bool = Field(default=True, description="Whether the operation was successful")
    message: str = Field(default="Runner code generated successfully", description="Status message")


def _reject_if_requested_pipe_skipped(sweep_result: dict[str, DryRunOutput], pipe_code: str) -> None:
    """Reject when the *requested* pipe was SKIPPED (its cross-package dependency is unresolved).

    `validate_pipes` records a pipe SKIPPED — rather than failing the whole sweep — when a controller
    references a sub-pipe in a package not included in the request (the cross-package tolerance shared
    with `/validate` and `/build/{inputs,output}`). For a code-generation endpoint that is a footgun:
    `generate_runner_code` reads only the pipe's own inputs/output and never resolves sub-pipes, so it
    would happily emit runner code for a pipeline that cannot actually run. Reject the *requested* pipe
    being SKIPPED with a 422; unrelated SKIPPED pipes elsewhere in the bundle stay tolerated.
    """
    for output in sweep_result.values():
        if pipe_code not in (output.pipe_code, output.pipe_ref):
            continue
        match output.status:
            case DryRunStatus.SKIPPED:
                detail = output.error_message or "its dependencies could not be resolved"
                raise_validation_error(f"Cannot generate runner code for pipe '{pipe_code}': {detail}")
            case DryRunStatus.SUCCESS | DryRunStatus.FAILURE:
                return


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
        try:
            sweep_result = await BundleValidator().validate_pipes(pipes=pipes, library_id=library_id, allow_signatures=request_data.allow_signatures)
        except DryRunError as dry_run_error:
            # `validate_pipes` raises a bare `DryRunError` on a non-allowed dry-run failure. This route
            # calls the inner sweep directly instead of through `validate_bundle`, so nothing translates
            # it — and `DryRunError` carries no `error_domain`, which the global handler would render as a
            # 500 server fault. A failed dry-run of a caller-submitted bundle is a caller-fixable INPUT
            # error, so translate it to `ValidateBundleError` (error_domain=INPUT → 422) — the exact shape
            # `/validate`, `/build/inputs`, and `/build/output` return for the identical failure via
            # `validate_bundle`'s `_translate_to_validate_bundle_error`. (`SignaturesNotAllowedError` is a
            # sibling class, not a `DryRunError`, and already carries error_domain=INPUT, so it is left to
            # propagate untouched and still renders as 422.)
            raise ValidateBundleError(
                message=dry_run_error.message,
                dry_run_error_message=dry_run_error.message,
            ) from dry_run_error

        # The sweep tolerates a cross-package unresolved dependency by recording the pipe SKIPPED
        # instead of failing. Don't hand back runner code for the *requested* pipe in that state.
        _reject_if_requested_pipe_skipped(sweep_result, request_data.pipe_code)

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
