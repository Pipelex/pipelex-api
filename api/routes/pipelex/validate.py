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
from pipelex.pipeline.validate_bundle import ValidateBundleError, validate_bundle
from pydantic import BaseModel, Field, field_validator

from api.limits import MAX_MTHDS_FILE_BYTES, MAX_MTHDS_FILES_PER_REQUEST

router = APIRouter(tags=["validate"])


class ValidateRequest(BaseModel):
    mthds_contents: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_MTHDS_FILES_PER_REQUEST,
        description="MTHDS contents to validate (always an array, even for single file).",
    )

    @field_validator("mthds_contents")
    @classmethod
    def _bound_each_file(cls, value: list[str]) -> list[str]:
        for content in value:
            if len(content.encode("utf-8")) > MAX_MTHDS_FILE_BYTES:
                msg = f"MTHDS file exceeds {MAX_MTHDS_FILE_BYTES // 1024} KiB limit"
                raise ValueError(msg)
        return value


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
    """Validate MTHDS content by parsing, loading, and dry-running pipes."""
    mthds_contents = request_data.mthds_contents

    try:
        validate_bundle_result = await validate_bundle(mthds_contents=mthds_contents)
    except ValidateBundleError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "mthds_contents": mthds_contents,
                "message": str(exc),
            },
        )

    primary_blueprint = _find_main_blueprint(validate_bundle_result.blueprints) or validate_bundle_result.blueprints[0]

    if not primary_blueprint.main_pipe:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "mthds_contents": mthds_contents,
                "message": "Bundle does not declare a main_pipe, which is required for validation",
            },
        )

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
        # Best-effort only: dry-run is a pipelex operation, so its failures are
        # PipelexError subclasses. The bundle is already validated — a dry-run
        # failure just means no graph, not a failed request.
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
