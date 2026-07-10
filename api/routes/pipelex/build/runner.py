from typing import Annotated, Literal, Union

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from mthds.package.manifest.schema import MTHDS_STANDARD_VERSION
from pipelex.base_exceptions import PipelexUnexpectedError
from pipelex.builder.runner_code import generate_runner_code
from pipelex.codegen.emission import build_stamped_projection
from pipelex.codegen.emitters.naming import runtime_to_emitted_class_names
from pipelex.codegen.emitters.target import CodegenKind, CodegenTarget
from pipelex.codegen.emitters.types_emitter import emit_types
from pipelex.codegen.lock import CODEGEN_LOCK_FILENAME
from pipelex.codegen.resolved_concepts import resolve_concepts_from_crate
from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
from pipelex.core.pipes.variable_multiplicity import parse_concept_with_multiplicity
from pipelex.hub import clear_current_library, get_current_library_id_or_none, get_library_manager, get_required_pipe
from pipelex.libraries.crate_normalization import normalize_crate
from pipelex.pipeline.bundle_validator import DryRunOutput, DryRunStatus
from pipelex.pipeline.exceptions import ValidateBundleError
from pipelex.pipeline.validate_bundle import validate_bundle
from pipelex.tools.misc.package_utils import get_package_version
from pipelex.tools.typing.pydantic_utils import empty_list_factory_of
from pydantic import BaseModel, Field

from api.errors import raise_validation_error
from api.limits import MAX_PIPE_CODE_LEN
from api.routes.pipelex.crate_ops import CrateInvalidReport, GeneratedArtifact, invalid_crate_report_response
from api.schemas.models import MthdsContentsRequest

router = APIRouter(tags=["build"])


class BuildRunnerRequest(MthdsContentsRequest):
    pipe_code: str = Field(..., min_length=1, max_length=MAX_PIPE_CODE_LEN, description="Pipe code to generate runner code for.")


class RunnerStructures(BaseModel):
    """The typed-structures projection the runner script imports from (`from structures.structures import ...`).

    The same stamped `python-structures` artifacts + `codegen.lock` a local `pipelex build runner`
    scaffolds into `<output>/structures/` — write `artifacts` and the lock under `directory` and
    the returned `python_code` runs against them, with the offline `codegen check` passing there.
    """

    directory: str = Field(default="structures", description="Directory (relative to the runner script) to write the artifacts and lock into.")
    artifacts: list[GeneratedArtifact] = Field(
        default_factory=empty_list_factory_of(GeneratedArtifact),
        description="The stamped generated files (paths relative to `directory`).",
    )
    lock: str = Field(..., description="The lock content (TOML) tracking the artifact set — write verbatim inside `directory`.")
    lock_filename: str = Field(default=CODEGEN_LOCK_FILENAME, description="Filename the lock content must be written as.")


class BuildRunnerValidReport(BaseModel):
    """The 200 **valid** arm: the runner script plus the structures projection it imports from."""

    is_valid: Literal[True] = True
    pipe_code: str = Field(..., description="The pipe the runner was generated for (echo of the request).")
    python_code: str = Field(..., description="Generated Python script for running the pipeline, imports spelled with the emitted class names.")
    structures: RunnerStructures = Field(..., description="The typed-structures projection the script imports from.")
    message: str = Field(default="Runner code generated successfully", description="Status message")


# Discriminated 200 response union: the `/validate` discipline — the verdict rides `is_valid`,
# never the HTTP status (breaking change from the previous `success`-bool body + 422-on-invalid).
BuildRunnerResponse = Annotated[Union[BuildRunnerValidReport, CrateInvalidReport], Field(discriminator="is_valid")]


def _reject_if_requested_pipe_skipped(sweep_result: dict[str, DryRunOutput], *, pipe_code: str) -> None:
    """Reject when the *requested* pipe was SKIPPED (its cross-package dependency is unresolved).

    `validate_pipes` records a pipe SKIPPED — rather than failing the whole sweep — when a controller
    references a sub-pipe in a package not included in the request (the cross-package tolerance shared
    with `/validate` and `/build/{inputs,output}`). For a code-generation endpoint that is a footgun:
    `generate_runner_code` reads only the pipe's own inputs/output and never resolves sub-pipes, so it
    would happily emit runner code for a pipeline that cannot actually run. A SKIPPED requested pipe is
    a no-verdict condition (its dependency closure is absent from the request, so nothing could be
    diagnosed) → request-shape 422; unrelated SKIPPED pipes elsewhere in the bundle stay tolerated.
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


def _output_is_list(blueprints: list[PipelexBundleBlueprint], *, pipe_code: str) -> bool:
    """Whether the requested pipe's declared output carries a list multiplicity marker (mirrors the CLI)."""
    for blueprint in blueprints:
        if blueprint.pipe and pipe_code in blueprint.pipe:
            output_parse = parse_concept_with_multiplicity(blueprint.pipe[pipe_code].output)
            return output_parse.multiplicity is not None
    return False


@router.post("/build/runner", response_model=BuildRunnerResponse)
async def build_runner(request_data: BuildRunnerRequest) -> JSONResponse:
    """Generate a Python runner script for a pipe, riding the codegen types projection (D9).

    `validate_bundle` opens a single library, loads the bundle, runs the sweep scoped to the
    requested pipe, and on success leaves the library loaded + current; on failure it tears it down
    itself. On the success path the crate is read from that library (built only from a valid
    library), the `python-structures` projection is emitted and stamped, and the runner script is
    generated with the **emitted** class names — the same flow as a local `pipelex build runner`.

    Response contract (the `/validate` discipline): an invalid bundle — including a failed dry-run
    of the requested pipe — is a produced verdict: a **200** `is_valid: false` with the structured
    `validation_errors[]`. Non-2xx is reserved for no-verdict conditions: a request-shape 422
    (including a requested pipe whose cross-package dependencies are absent from the request), auth,
    server fault — RFC 7807 via the global handlers.
    """
    library_manager = get_library_manager()

    try:
        # If this raises, validate_bundle has already torn down its own library — nothing to clean up here.
        validate_result = await validate_bundle(
            mthds_contents=request_data.mthds_contents,
            allow_signatures=request_data.allow_signatures,
            dry_run_pipe_codes=[request_data.pipe_code],
        )
    except ValidateBundleError as validate_error:
        return invalid_crate_report_response(validate_error.to_error_report())

    # Success: validate_bundle left its library loaded + current. Build everything from it, then own its teardown.
    library_id = get_current_library_id_or_none()
    try:
        # The sweep tolerates a cross-package unresolved dependency by recording the pipe SKIPPED
        # instead of failing. Don't hand back runner code for the *requested* pipe in that state.
        _reject_if_requested_pipe_skipped(validate_result.dry_run_result, pipe_code=request_data.pipe_code)

        crate = library_manager.get_crate(library_id) if library_id else None
        if crate is None:
            # Unreachable after a successful in-memory validate (the blueprints were accumulated),
            # so a None crate is an internal invariant break — a server fault (5xx), never a
            # caller-facing verdict (mirrors resolve_crate_from_contents's identical guard).
            msg = "library crate unavailable after a successful bundle load"
            raise PipelexUnexpectedError(msg)
        normalized_crate = normalize_crate(crate, mthds_version=MTHDS_STANDARD_VERSION)
        emitted = emit_types(normalized_crate, target=CodegenTarget.PYTHON_STRUCTURES)
        projection = build_stamped_projection(
            emitted,
            crate_fingerprint=normalized_crate.fingerprint,
            engine_version=get_package_version(),
            kind=CodegenKind.TYPES,
            target=CodegenTarget.PYTHON_STRUCTURES,
        )
        class_name_overrides = runtime_to_emitted_class_names(resolve_concepts_from_crate(normalized_crate))

        the_pipe = get_required_pipe(pipe_code=request_data.pipe_code)
        python_code = generate_runner_code(
            pipe=the_pipe,
            output_multiplicity=_output_is_list(validate_result.blueprints, pipe_code=request_data.pipe_code),
            class_name_overrides=class_name_overrides,
        )

        report = BuildRunnerValidReport(
            pipe_code=request_data.pipe_code,
            python_code=python_code,
            structures=RunnerStructures(
                artifacts=[GeneratedArtifact(path=stamped.filename, content=stamped.content) for stamped in projection.files],
                lock=projection.lock_content,
            ),
        )
        return JSONResponse(content=report.model_dump(mode="json", by_alias=True))
    finally:
        clear_current_library()
        if library_id is not None:
            library_manager.teardown(library_id=library_id)
