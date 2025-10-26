import traceback
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from kajson import kajson
from pipelex import log
from pipelex.client.pipeline_request_factory import PipelineRequestFactory
from pipelex.client.pipeline_response_factory import PipelineResponseFactory
from pipelex.client.protocol import PipelineRequest, PipelineResponse, PipelineState
from pipelex.core.interpreter import PipelexInterpreter
from pipelex.hub import get_library_manager
from pipelex.pipeline.execute import execute_pipeline
from pipelex.pipeline.start import start_pipeline
from pipelex.pipeline.validate_plx import validate_plx

from api.routes.helpers import extract_pipe_structures
from api.routes.pipelex.utils import get_current_iso_timestamp

router = APIRouter(tags=["pipeline"])


async def request_deserialization(request: Request) -> PipelineRequest:
    """Dependency that deserializes the request body using kajson"""
    body = await request.body()
    body_str = body.decode("utf-8")
    request_data = kajson.loads(body_str)
    return PipelineRequestFactory.make_from_body(request_data)


@router.post("/pipeline/execute", response_model=PipelineResponse)
async def execute(
    pipeline_request: Annotated[PipelineRequest, Depends(request_deserialization)],
):
    """Executes a pipe with the given memory and waits for completion.

    This endpoint can operate in two modes:
    1. If 'plx_content' is provided: validates, loads, and executes pipes from the PLX content
    2. If 'plx_content' is not provided: executes an already-loaded pipe

    This is a blocking operation that doesn't return until the pipe execution is complete.
    """
    try:
        created_at = get_current_iso_timestamp()
        pipe_output = await execute_pipeline(
            pipe_code=pipeline_request.pipe_code,
            plx_content=pipeline_request.plx_content,
            inputs=pipeline_request.inputs,
            output_name=pipeline_request.output_name,
            output_multiplicity=pipeline_request.output_multiplicity,
            dynamic_output_concept_code=pipeline_request.dynamic_output_concept_code,
        )
        blueprint = PipelexInterpreter(file_content=pipeline_request.plx_content).make_pipelex_bundle_blueprint()
        pipes = get_library_manager().load_from_blueprint(blueprint=blueprint)
        pipe_structures = extract_pipe_structures(pipes)
        get_library_manager().remove_from_blueprint(blueprint=blueprint)

        return PipelineResponseFactory.make_from_pipe_output(
            status="success",
            pipeline_run_id=pipe_output.pipeline_run_id,
            pipeline_state=PipelineState.COMPLETED,
            created_at=created_at,
            finished_at=get_current_iso_timestamp(),
            pipe_output=pipe_output,
            pipe_structures=pipe_structures,
        )

    except Exception as exc:
        log.error("Pipeline execution error details:")
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail={
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        ) from exc


@router.post("/pipeline/start", response_model=PipelineResponse)
async def start(
    pipeline_request: Annotated[PipelineRequest, Depends(request_deserialization)],
):
    """Starts a pipe execution with the given memory but does not wait for completion.

    This endpoint can operate in two modes:
    1. If 'plx_content' is provided: validates, loads pipes from the PLX content, then starts execution
    2. If 'plx_content' is not provided: starts execution of an already-loaded pipe

    This is a non-blocking operation that returns immediately with a workflow ID.

    Note: If plx_content is provided, pipes remain loaded after this call returns.
    """
    try:
        if pipeline_request.plx_content:
            raise HTTPException(status_code=400, detail="PLX content is not supported when using the route 'start'")
        if not pipeline_request.pipe_code:
            raise HTTPException(status_code=400, detail="Pipe code is required when using the route 'start'")
        created_at = get_current_iso_timestamp()
        pipeline_run_id, _ = await start_pipeline(
            pipe_code=pipeline_request.pipe_code,
            inputs=pipeline_request.inputs,
            output_name=pipeline_request.output_name,
            output_multiplicity=pipeline_request.output_multiplicity,
            dynamic_output_concept_code=pipeline_request.dynamic_output_concept_code,
        )

        response_data = PipelineResponse(
            pipeline_run_id=pipeline_run_id,
            pipeline_state=PipelineState.STARTED,
            created_at=created_at,
            pipe_output=None,
            main_stuff_name=None,
            status="success",
        )

        # If we have pipe structures, add them to the response
        if pipeline_request.plx_content:
            blueprint, pipes = await validate_plx(plx_content=pipeline_request.plx_content)
            full_response_data = {
                "pipeline_response": response_data.model_dump(),
                "pipe_structures": extract_pipe_structures(pipes),
            }
            get_library_manager().remove_from_blueprint(blueprint=blueprint)
            return JSONResponse(content=full_response_data)

    except Exception as exc:
        log.error("Pipeline start error details:")
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail={
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        ) from exc
