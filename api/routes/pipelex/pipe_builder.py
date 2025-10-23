import traceback
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pipelex import log
from pipelex.builder.bundle_spec import PipelexBundleSpec
from pipelex.builder.runner_code import generate_runner_code
from pipelex.core.interpreter import PipelexInterpreter
from pipelex.hub import get_library_manager, get_required_pipe
from pipelex.language.plx_factory import PlxFactory
from pipelex.pipe_run.dry_run import dry_run_pipes
from pipelex.pipeline.execute import execute_pipeline
from pydantic import BaseModel, Field

from api.routes.helpers import extract_pipe_structures

router = APIRouter(tags=["pipe-builder"])


class PipeBuilderRequest(BaseModel):
    brief: str = Field(..., description="Brief description of the pipeline to build")


class PipeBuilderResponse(BaseModel):
    plx_content: str = Field(..., description="Generated PLX content as string")
    pipelex_bundle_blueprint: dict[str, Any] = Field(..., description="Generated pipelex bundle blueprint")
    pipe_structures: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Structure class information for each pipe's inputs and output"
    )
    success: bool = Field(default=True, description="Whether the operation was successful")
    message: str = Field(default="Pipeline generated successfully", description="Status message")


@router.post("/pipe-builder/build", response_model=PipeBuilderResponse)
async def build_pipe(request_data: PipeBuilderRequest):
    """Build a pipeline from a brief description.

    This endpoint takes a brief description and generates both PLX content
    and the corresponding pipelex bundle blueprint, along with pipe structures.
    """
    library_manager = get_library_manager()
    blueprint = None

    try:
        # Execute the pipe_builder pipeline
        pipe_output = await execute_pipeline(
            pipe_code="pipe_builder",
            inputs={"brief": request_data.brief},
        )

        # Extract the pipelex bundle spec from the output
        pipelex_bundle_spec = pipe_output.working_memory.get_stuff_as(name="pipelex_bundle_spec", content_type=PipelexBundleSpec)

        # Generate PLX content from the blueprint
        blueprint = pipelex_bundle_spec.to_blueprint()
        plx_content = PlxFactory.make_plx_content(blueprint=blueprint)

        # Load pipes temporarily to extract structures
        pipes = library_manager.load_from_blueprint(blueprint=blueprint)
        pipe_structures = extract_pipe_structures(pipes)

        # Clean up: remove pipes from library
        library_manager.remove_from_blueprint(blueprint=blueprint)

        # Create the response
        response_data = PipeBuilderResponse(
            plx_content=plx_content,
            pipelex_bundle_blueprint=blueprint.model_dump(serialize_as_any=True),
            pipe_structures=pipe_structures,
            success=True,
            message="Pipeline generated successfully",
        )

        return JSONResponse(content=response_data.model_dump(serialize_as_any=True))

    except Exception as exc:
        log.error("Error details:")
        traceback.print_exc()

        # Try to clean up if blueprint was created
        try:
            if blueprint is not None:
                library_manager.remove_from_blueprint(blueprint=blueprint)
        except Exception as cleanup_error:
            log.error(f"Error during cleanup: {cleanup_error}")

        raise HTTPException(status_code=500, detail=str(exc)) from exc


class RunnerCodeRequest(BaseModel):
    plx_content: str = Field(..., description="PLX content to load and generate runner code for")
    pipe_code: str = Field(..., description="Pipe code to generate runner code for")


class RunnerCodeResponse(BaseModel):
    python_code: str = Field(..., description="Generated Python code for running the workflow")
    pipe_code: str = Field(..., description="Pipe code that was used")
    success: bool = Field(default=True, description="Whether the operation was successful")
    message: str = Field(default="Runner code generated successfully", description="Status message")


@router.post("/pipe-builder/generate-runner", response_model=RunnerCodeResponse)
async def generate_runner(request_data: RunnerCodeRequest):
    """Generate Python runner code for a pipe from PLX content.

    This endpoint:
    1. Parses and validates PLX content
    2. Loads pipes from the blueprint
    3. Validates and dry-runs all pipes
    4. Generates runner code for the specified pipe
    5. Cleans up loaded pipes
    6. Returns the generated Python code
    """
    library_manager = get_library_manager()
    blueprint = None

    try:
        # Parse PLX content into a bundle blueprint
        converter = PipelexInterpreter(file_content=request_data.plx_content)
        blueprint = converter.make_pipelex_bundle_blueprint()

        # Load pipes from the blueprint
        pipes = library_manager.load_from_blueprint(blueprint=blueprint)

        # Validate all pipes
        for pipe in pipes:
            pipe.validate_with_libraries()
            await dry_run_pipes(pipes=[pipe], raise_on_failure=True)

        # Get the required pipe and generate runner code
        pipe = get_required_pipe(request_data.pipe_code)
        python_code = generate_runner_code(pipe=pipe)

        # Clean up: remove pipes from library
        library_manager.remove_from_blueprint(blueprint=blueprint)

        # Create the response
        response_data = RunnerCodeResponse(
            python_code=python_code,
            pipe_code=request_data.pipe_code,
            success=True,
            message="Runner code generated successfully",
        )

        return JSONResponse(content=response_data.model_dump(serialize_as_any=True))

    except Exception as exc:
        log.error(f"Error generating runner code for pipe '{request_data.pipe_code}':")
        traceback.print_exc()

        # Always clean up if blueprint was created
        try:
            if blueprint is not None:
                library_manager.remove_from_blueprint(blueprint=blueprint)
        except Exception as cleanup_error:
            log.error(f"Error during cleanup: {cleanup_error}")

        raise HTTPException(status_code=500, detail=str(exc)) from exc
