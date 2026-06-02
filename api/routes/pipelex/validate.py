from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pipelex import log
from pipelex.base_exceptions import PipelexError
from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
from pipelex.core.concepts.concept_representation_generator import ConceptRepresentationFormat
from pipelex.core.pipes.pipe_abstract import PipeAbstract
from pipelex.graph.graphspec import GraphSpec
from pipelex.pipe_run.dry_run_pipeline import dry_run_pipeline
from pipelex.pipeline.validate_bundle import validate_bundle
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

    Response contract:
    - **Success (200):** the `ValidateResponse` envelope — the validated
      `pipelex_bundle_blueprint`, the best-effort `graph_spec`, per-pipe
      `pipe_structures`, plus the `success` / `message` flags clients use to
      gate UI on a known-good bundle.
    - **Failure (422):** RFC 7807 `application/problem+json` — same shape as
      every other API endpoint. `ValidateBundleError` is a `PipelexError`
      (`error_domain = INPUT`) so it propagates to the global handler in
      `api.exception_handlers` unchanged; "bundle has no `main_pipe`" is an API-side
      semantic precondition for this endpoint and is raised via
      `raise_validation_error`. The legacy
      `{success: false, mthds_contents, message}` envelope is gone — its 422
      half lost the structured per-pipe error data carried on
      `ValidateBundleError`, and its 400 half made `/validate` the only
      endpoint that did not emit RFC 7807. Both are now the uniform shape.
    """
    mthds_contents = request_data.mthds_contents

    # `ValidateBundleError` (and any other `PipelexError`) propagates: the
    # global `PipelexError` handler turns it into an RFC 7807 422 carrying
    # `error_type=ValidateBundleError`, `error_domain=input`, the verbatim
    # message (caller-facing under `_authors_caller_facing_message`), the
    # docs `type_uri`, and the `user_action` hint. We do not catch and
    # re-shape it here — Phase 3 deleted every per-route error catch.
    validate_bundle_result = await validate_bundle(mthds_contents=mthds_contents, allow_signatures=request_data.allow_signatures)

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
