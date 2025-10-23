from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pipelex.core.bundles.pipelex_bundle_blueprint import PipelexBundleBlueprint
from pipelex.pipeline.validate_plx import validate_plx
from pydantic import BaseModel, Field

from api.routes.helpers import extract_pipe_structures

router = APIRouter()


class PlxValidatorRequest(BaseModel):
    plx_content: str = Field(..., description="PLX content to validate")


class PlxValidatorResponse(BaseModel):
    plx_content: str = Field(..., description="The PLX content that was validated")
    pipelex_bundle_blueprint: PipelexBundleBlueprint = Field(..., description="Generated pipelex bundle blueprint")
    pipe_structures: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Structure class information for each pipe's inputs and output"
    )
    success: bool = Field(default=True, description="Whether the validation was successful")
    message: str = Field(default="PLX content validated successfully", description="Status message")


@router.post("/validate", response_model=PlxValidatorResponse)
async def validate_pipes(request_data: PlxValidatorRequest):
    """Validate PLX content by parsing, loading, and dry-running pipes.

    This endpoint takes PLX content and validates it by:
    1. Parsing it into a bundle blueprint
    2. Loading pipes into the library
    3. Running static validation and dry runs

    Returns validation results with blueprint and pipe structures.
    """
    blueprint, pipes = await validate_plx(plx_content=request_data.plx_content)
    pipe_structures = extract_pipe_structures(pipes)

    response_data = PlxValidatorResponse(
        plx_content=request_data.plx_content,
        pipelex_bundle_blueprint=blueprint,
        pipe_structures=pipe_structures,
        success=True,
        message="PLX content validated successfully",
    )

    return JSONResponse(content=response_data.model_dump(serialize_as_any=True))
