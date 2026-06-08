"""Runner-native async run lifecycle: start → poll → result, by bare id.

The standard async request-reply pattern for long pipelines, served by the
open-source runner itself (no Temporal, no webhook receiver, no external store):

    POST /api/v1/runs                       -> { pipeline_run_id, status, created_at }
    GET  /api/v1/runs/by-id/{id}            -> { pipeline_run_id, status, created_at, finished_at? }
    GET  /api/v1/runs/by-id/{id}/result     -> 202 running (+ Retry-After)
                                               200 completed (the execute response)
                                               409 failed (terminal, with message)

These paths mirror the hosted platform's run surface so the SDK drives both with
one code path; the record here is lean and identity-free (the platform layers
org/user/method on top). State is kept in the app's `RunStore` (in-memory by
default) and execution runs as an in-process background task.
"""

import asyncio
import uuid
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from mthds.client.pipeline import PipelineRequest
from pipelex import log
from pydantic import BaseModel, Field

from api.error_types import ErrorType
from api.errors import ENDPOINT_HANDLED_EXCEPTIONS
from api.routes.pipelex.pipeline import ApiRunner, get_request_user_id, parse_pipeline_request
from api.run_store import LeanRunStatus, RunStore

router = APIRouter(tags=["runs"])

# While a run is in flight, ask pollers to wait this long between result checks.
_RESULT_RETRY_AFTER_SECONDS = 2


class RunStartResponse(BaseModel):
    pipeline_run_id: str = Field(..., description="Id to poll the run by.")
    status: LeanRunStatus = Field(..., description="Lifecycle status at creation (PENDING/RUNNING).")
    created_at: str = Field(..., description="ISO-8601 creation timestamp.")


class RunStatusResponse(BaseModel):
    pipeline_run_id: str = Field(..., description="The run id.")
    status: LeanRunStatus = Field(..., description="Current lifecycle status.")
    created_at: str = Field(..., description="ISO-8601 creation timestamp.")
    finished_at: str | None = Field(default=None, description="ISO-8601 completion timestamp, once terminal.")


def _get_run_store(request: Request) -> RunStore:
    """The process-wide run store, created in the app lifespan."""
    store: RunStore = request.app.state.run_store
    return store


async def _run_in_background(
    pipeline_run_id: str,
    pipeline_request: PipelineRequest,
    user_id: str,
    store: RunStore,
) -> None:
    """Execute the pipeline off the request path and record its terminal state.

    Catches the same curated exception set as the blocking `/pipeline/execute`
    handler — those are the failures `execute_pipeline` is known to raise — and
    records them as FAILED so a poller sees a clean 409 instead of a stuck run.
    """
    await store.mark_running(pipeline_run_id)
    try:
        runner = ApiRunner(user_id=user_id)
        response = await runner.execute_pipeline(
            pipe_code=pipeline_request.pipe_code,
            mthds_contents=pipeline_request.mthds_contents,
            inputs=pipeline_request.inputs,
            output_name=pipeline_request.output_name,
            output_multiplicity=pipeline_request.output_multiplicity,
            dynamic_output_concept_ref=pipeline_request.dynamic_output_concept_ref,
        )
        await store.mark_completed(pipeline_run_id, response)
    except ENDPOINT_HANDLED_EXCEPTIONS as exc:
        message = str(exc)
        log.error(f"Async run {pipeline_run_id} failed: {message}")
        await store.mark_failed(pipeline_run_id, message)


@router.post(
    "/runs",
    summary="Start a run asynchronously and poll it by id",
    description=(
        "Starts the pipeline as a background run and returns its `pipeline_run_id` "
        "immediately. Poll `GET /runs/by-id/{id}` for status and "
        "`GET /runs/by-id/{id}/result` for the output — the standard async pattern for "
        "long pipelines, with no held connection. Same request body as `/pipeline/execute`."
    ),
)
async def start_run(request: Request) -> RunStartResponse:
    pipeline_request, _extras = await parse_pipeline_request(request)
    user_id = get_request_user_id(request)
    store = _get_run_store(request)

    pipeline_run_id = str(uuid.uuid4())
    record = await store.create(pipeline_run_id)

    task = asyncio.create_task(_run_in_background(pipeline_run_id, pipeline_request, user_id, store))
    # Hold a strong reference so the task isn't garbage-collected mid-flight.
    request.app.state.run_tasks.add(task)
    task.add_done_callback(request.app.state.run_tasks.discard)

    return RunStartResponse(pipeline_run_id=record.pipeline_run_id, status=record.status, created_at=record.created_at)


@router.get(
    "/runs/by-id/{pipeline_run_id}",
    summary="Get a run's status by id",
)
async def get_run(pipeline_run_id: str, request: Request) -> RunStatusResponse:
    store = _get_run_store(request)
    record = await store.get(pipeline_run_id)
    if record is None:
        _raise_run_not_found(pipeline_run_id)
    return RunStatusResponse(
        pipeline_run_id=record.pipeline_run_id,
        status=record.status,
        created_at=record.created_at,
        finished_at=record.finished_at,
    )


@router.get(
    "/runs/by-id/{pipeline_run_id}/result",
    summary="Get a run's result by id (202 running / 200 completed / 409 failed)",
    description=(
        "Single-shot result lookup. **202** while the run is pending/running (retry after "
        "`Retry-After` seconds); **200** with the execute response when COMPLETED; **409** "
        "when the run reached a terminal failure."
    ),
)
async def get_run_result(pipeline_run_id: str, request: Request) -> Response:
    store = _get_run_store(request)
    record = await store.get(pipeline_run_id)
    if record is None:
        _raise_run_not_found(pipeline_run_id)

    match record.status:
        case LeanRunStatus.PENDING | LeanRunStatus.RUNNING:
            return Response(status_code=202, headers={"Retry-After": str(_RESULT_RETRY_AFTER_SECONDS)})
        case LeanRunStatus.COMPLETED:
            result = record.result
            if result is None:
                # COMPLETED is only set together with a result; treat a missing one as transient.
                return Response(status_code=202, headers={"Retry-After": str(_RESULT_RETRY_AFTER_SECONDS)})
            return JSONResponse(content=result.model_dump(mode="json", serialize_as_any=True, by_alias=True))
        case LeanRunStatus.FAILED:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "error_type": ErrorType.VALIDATION_ERROR,
                        "message": record.error or "Run failed",
                        "status": record.status,
                    }
                },
            )


def _raise_run_not_found(pipeline_run_id: str) -> NoReturn:
    message = f"No run found with id {pipeline_run_id!r}"
    raise HTTPException(
        status_code=404,
        detail={"error_type": ErrorType.RUN_NOT_FOUND, "message": message},
    )
