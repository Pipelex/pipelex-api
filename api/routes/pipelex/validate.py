from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pipelex import log
from pipelex.base_exceptions import PipelexError
from pipelex.config import get_config
from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
from pipelex.core.concepts.concept_representation_generator import ConceptRepresentationFormat
from pipelex.core.interpreter.interpreter import PipelexInterpreter
from pipelex.core.pipes.pipe_abstract import PipeAbstract
from pipelex.core.pipes.pipe_factory import PipeFactory
from pipelex.graph.graphspec import GraphSpec
from pipelex.hub import clear_current_library, get_current_library_id_or_none, get_library_manager, get_required_pipe
from pipelex.pipe_run.dry_run_pipeline import dry_run_pipeline
from pipelex.pipeline.execution_seams import acquire_library
from pipelex.pipeline.validate_bundle import validate_bundle
from pipelex.temporal.tprl_pipe.act_dry_validate import DryValidateArg
from pipelex.temporal.tprl_pipe.dry_validate_dispatch import dispatch_dry_validate
from pydantic import BaseModel, Field

from api.errors import raise_validation_error
from api.schemas.models import MthdsContentsRequest

router = APIRouter(tags=["validate"])


class ValidateRequest(MthdsContentsRequest):
    """`/validate` needs nothing beyond the shared `mthds_contents` + `allow_signatures` payload."""


class ValidateResponse(BaseModel):
    mthds_contents: list[str] = Field(..., description="The MTHDS contents that were validated")
    pipelex_bundle_blueprint: PipelexBundleBlueprint = Field(..., description="Generated pipelex bundle blueprint")
    graph_spec: GraphSpec | None = Field(default=None, description="Graph spec from the dry run")
    pipe_structures: dict[str, Any] = Field(default_factory=dict, description="Per-pipe input/output JSON Schema structures")
    success: bool = Field(default=True, description="Whether the validation was successful")
    message: str = Field(default="MTHDS content validated successfully", description="Status message")


def _build_pipe_structures(pipes: list[PipeAbstract]) -> dict[str, Any]:
    """Build per-pipe input/output structures with JSON Schema from pydantic models."""
    structures: dict[str, Any] = {}
    for pipe in pipes:
        pipe_inputs: dict[str, Any] = {}
        if pipe.inputs and pipe.inputs.root:
            for var_name, stuff_spec in pipe.inputs.root.items():
                schema_repr = stuff_spec.render_stuff_spec(ConceptRepresentationFormat.SCHEMA)
                pipe_inputs[var_name] = {
                    "concept_code": schema_repr.get("concept", ""),
                    "json_schema": schema_repr.get("content", {}),
                }

        pipe_output: dict[str, Any] = {}
        if pipe.output:
            pipe_output = {
                "concept_code": pipe.output.concept.concept_ref,
                "multiplicity": "single" if not pipe.output.is_multiple() else "variable",
            }

        structures[pipe.code] = {
            "inputs": pipe_inputs,
            "output": pipe_output,
        }
    return structures


def _find_main_blueprint(blueprints: list[PipelexBundleBlueprint]) -> PipelexBundleBlueprint | None:
    """Find the first blueprint that declares a main_pipe."""
    for blueprint in blueprints:
        if blueprint.main_pipe:
            return blueprint
    return None


@router.post("/validate", response_model=ValidateResponse)
async def validate_mthds(request_data: ValidateRequest) -> JSONResponse:
    """Validate MTHDS content by parsing, loading, and dry-running pipes.

    Two backends, one contract:

    - **Direct (Temporal disabled):** runs `validate_bundle` (sweep) + `dry_run_pipeline`
      (graph) in-process — unchanged.
    - **Temporal enabled:** dispatches the whole job — sweep **+** graph dry-run — to a worker
      as ONE in-process activity (`wf_dry_validate` → `act_dry_validate`) and awaits
      `{status map, graph_spec}` in a single round-trip. The activity runs `validate_bundle`
      itself and traces the graph in memory, so the error contract and the best-effort-graph
      semantics are identical to the direct path.

    Response contract:
    - **Success (200):** the `ValidateResponse` envelope — the validated
      `pipelex_bundle_blueprint`, the best-effort `graph_spec`, per-pipe
      `pipe_structures`, plus the `success` / `message` flags clients use to
      gate UI on a known-good bundle.
    - **Failure (422):** RFC 7807 `application/problem+json` — same shape as
      every other API endpoint. Direct mode: `ValidateBundleError` is a `PipelexError`
      (`error_domain = INPUT`) and propagates to the global handler in
      `api.exception_handlers` unchanged. Temporal mode: the same failure crosses the
      activity boundary as a structured `ErrorReport` and surfaces as
      `WorkflowExecutionError` — also a `PipelexError` — whose `to_error_report()`
      returns the recovered original report (`error_type=ValidateBundleError`,
      `error_domain=input`, caller-facing message), so the handler renders the SAME
      problem document. "Bundle has no `main_pipe`" is an API-side semantic
      precondition for this endpoint and is raised via `raise_validation_error`.
    """
    if get_config().temporal.is_enabled:
        return await _validate_via_temporal(request_data)
    return await _validate_direct(request_data)


async def _validate_via_temporal(request_data: ValidateRequest) -> JSONResponse:
    """Temporal backend: ONE worker round-trip for the whole sweep + graph dry-run."""
    mthds_contents = request_data.mthds_contents

    # Dispatch FIRST — before any API-side parsing — so every validation failure (malformed
    # TOML, factory/wiring errors, unexpected dry-run failures, strict-mode signature refusals)
    # surfaces through the worker's `validate_bundle` cascade with the exact same categorized
    # `ValidateBundleError` identity the direct path raises. No route-side catch: the
    # `WorkflowExecutionError` carrying the recovered report propagates to the global handler.
    dry_validate_result = await dispatch_dry_validate(DryValidateArg(mthds_contents=mthds_contents, allow_signatures=request_data.allow_signatures))

    # The bundle is known-valid now — parse the blueprints for the response envelope.
    blueprints = [PipelexInterpreter.make_pipelex_bundle_blueprint(mthds_content=content) for content in mthds_contents]
    primary_blueprint = _find_main_blueprint(blueprints) or blueprints[0]

    if not primary_blueprint.main_pipe:
        # Same API-side semantic precondition as the direct path; checked after the dispatch so
        # validation errors keep precedence over the missing-main_pipe error.
        raise_validation_error("Bundle does not declare a main_pipe, which is required for validation")

    # `pipe_structures` need resolved pipes (concept refs resolve against a loaded library), so
    # load the validated bundle locally — load only: the sweep and the graph already ran on the
    # worker, nothing here dry-runs or traces.
    library_id, _ = acquire_library(library_id="", mthds_contents=mthds_contents)
    try:
        pipes: list[PipeAbstract] = []
        for blueprint in blueprints:
            for pipe_code in blueprint.pipe or {}:
                pipe_ref = PipeFactory.make_pipe_ref_with_domain(domain_code=blueprint.domain, pipe_code=pipe_code)
                pipes.append(get_required_pipe(pipe_code=pipe_ref))
        pipe_structures = _build_pipe_structures(pipes)
    finally:
        clear_current_library()
        get_library_manager().teardown(library_id=library_id)

    response_data = ValidateResponse(
        mthds_contents=mthds_contents,
        pipelex_bundle_blueprint=primary_blueprint,
        graph_spec=dry_validate_result.graph_spec,
        pipe_structures=pipe_structures,
        success=True,
        message="MTHDS content validated successfully",
    )
    return JSONResponse(content=response_data.model_dump(mode="json", serialize_as_any=True, by_alias=True))


async def _validate_direct(request_data: ValidateRequest) -> JSONResponse:
    """Direct backend: in-process `validate_bundle` + best-effort `dry_run_pipeline` (unchanged)."""
    mthds_contents = request_data.mthds_contents

    # `ValidateBundleError` (and any other `PipelexError`) propagates: the
    # global `PipelexError` handler turns it into an RFC 7807 422 carrying
    # `error_type=ValidateBundleError`, `error_domain=input`, the verbatim
    # message (caller-facing under `_authors_caller_facing_message`), the
    # docs `type_uri`, and the `user_action` hint. We do not catch and
    # re-shape it here — Phase 3 deleted every per-route error catch.
    validate_bundle_result = await validate_bundle(mthds_contents=mthds_contents, allow_signatures=request_data.allow_signatures)

    # `validate_bundle` leaves its library loaded + current on success (the D6 loaded-on-success
    # contract — its own teardown only fires on failure). Own that teardown here, exactly like
    # `/build/inputs` and `/build/output`, so the library is not orphaned in the LibraryManager on
    # every successful call. The `dry_run_pipeline` graph step below opens and tears down its OWN
    # library and restores this one as current, so capturing the id now (before that call) pins the
    # right library to clean up.
    library_manager = get_library_manager()
    library_id = get_current_library_id_or_none()
    try:
        primary_blueprint = _find_main_blueprint(validate_bundle_result.blueprints) or validate_bundle_result.blueprints[0]

        if not primary_blueprint.main_pipe:
            # API-side semantic precondition: a bundle without `main_pipe` parsed
            # cleanly but is unusable for this endpoint's purpose. 422 (not 400):
            # the request body is syntactically valid; what failed is its content
            # against this endpoint's domain rule.
            raise_validation_error("Bundle does not declare a main_pipe, which is required for validation")

        pipe_structures = _build_pipe_structures(validate_bundle_result.pipes)

        # Best-effort dry-run for graph generation. The bundle is already validated
        # at this point — if dry-run fails (e.g., a pipe needs runtime inputs that
        # mock_inputs can't synthesize), we still return success with the validated
        # bundle and skip the graph. Run-time errors during dry-run are not
        # validation errors.
        graph_spec: GraphSpec | None = None
        try:
            graph_spec, _ = await dry_run_pipeline(mthds_contents=mthds_contents)
        except PipelexError as exc:
            # Best-effort only. The bundle is already validated, so an expected dry-run
            # failure (mock_inputs can't satisfy a pipe, pipe resolution, etc.) is a
            # PipelexError subclass and just means "no graph", not a failed request.
            # The catch is intentionally narrow: a non-PipelexError escape — e.g. a
            # KeyError/TypeError from pipelex's graph assembler — is a genuine bug, not
            # an expected dry-run outcome, and is left to surface as a 500 via the
            # global handler. pipelex's own assemble_graph_on_output lets such
            # programming bugs propagate by design.
            log.warning(f"validate_mthds: dry-run did not produce a graph ({type(exc).__name__}); returning bundle without graph_spec")

        response_data = ValidateResponse(
            mthds_contents=mthds_contents,
            pipelex_bundle_blueprint=primary_blueprint,
            graph_spec=graph_spec,
            pipe_structures=pipe_structures,
            success=True,
            message="MTHDS content validated successfully",
        )

        return JSONResponse(content=response_data.model_dump(mode="json", serialize_as_any=True, by_alias=True))
    finally:
        clear_current_library()
        if library_id is not None:
            library_manager.teardown(library_id=library_id)
