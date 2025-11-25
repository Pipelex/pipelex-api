import importlib.util
import traceback
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pipelex import log
from pipelex.builder.builder_loop import BuilderLoop
from pipelex.builder.runner_code import generate_runner_code
from pipelex.core.interpreter.interpreter import PipelexInterpreter
from pipelex.hub import get_library_manager, get_required_pipe, set_current_library, teardown_current_library
from pipelex.language.plx_factory import PlxFactory
from pipelex.pipe_run.dry_run import dry_run_pipes
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
    # Execute the pipe_builder pipeline
    library_manager = get_library_manager()
    library_id, library = library_manager.open_library()
    set_current_library(library_id)

    # Find the pipelex package, then get the builder subdirectory path
    pipelex_spec = importlib.util.find_spec("pipelex")
    if pipelex_spec is None or pipelex_spec.origin is None:
        msg = "Could not find pipelex package"
        raise ImportError(msg)
    pipelex_path = Path(pipelex_spec.origin).parent
    pipelex_builder_path = pipelex_path / "builder"

    library_manager.load_libraries(library_id=library_id, library_dirs=[pipelex_builder_path])
    builder_loop = BuilderLoop()
    pipelex_bundle_spec = await builder_loop.build_and_fix(inputs={"brief": request_data.brief}, builder_pipe="pipe_builder")
    blueprint = pipelex_bundle_spec.to_blueprint()

    library.teardown()
    teardown_current_library()

    library_id, _ = library_manager.open_library()
    set_current_library(library_id)
    # Load pipes temporarily to extract structures
    pipes = library_manager.load_from_blueprints(library_id=library_id, blueprints=[blueprint])
    pipe_structures = extract_pipe_structures(pipes)

    plx_content = PlxFactory.make_plx_content(blueprint=blueprint)
    response_data = PipeBuilderResponse(
        plx_content=plx_content,
        pipelex_bundle_blueprint=blueprint.model_dump(serialize_as_any=True),
        pipe_structures=pipe_structures,
        success=True,
        message="Pipeline generated successfully",
    )

    return JSONResponse(content=response_data.model_dump(serialize_as_any=True))


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

    library_id, _ = library_manager.open_library()
    set_current_library(library_id)
    try:
        # Parse PLX content into a bundle blueprint
        converter = PipelexInterpreter()
        blueprint = converter.make_pipelex_bundle_blueprint(plx_content=request_data.plx_content)

        # Load pipes from the blueprint
        pipes = library_manager.load_from_blueprints(library_id=library_id, blueprints=[blueprint])

        # Validate all pipes
        for pipe in pipes:
            pipe.validate_with_libraries()
            await dry_run_pipes(pipes=[pipe], raise_on_failure=True)

        # Get the required pipe and generate runner code
        pipe = get_required_pipe(request_data.pipe_code)
        python_code = generate_runner_code(pipe=pipe)

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

        raise HTTPException(status_code=500, detail=str(exc)) from exc
