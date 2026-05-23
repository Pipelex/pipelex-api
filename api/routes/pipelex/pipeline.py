from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kajson import kajson
from kajson.exceptions import KajsonDecoderError
from mthds.client.exceptions import PipelineRequestError
from mthds.client.pipeline import PipelineRequest, PipelineState
from pipelex.config import get_config
from pipelex.pipe_run.delivery_assignment import DeliveryAssignment, StorageTarget, WebhookTarget
from pipelex.pipeline.pipeline_response import PipelexPipelineExecuteResponse, PipelexPipelineStartResponse
from pipelex.pipeline.pipeline_run_setup import pipeline_run_setup
from pipelex.pipeline.runner import PipelexRunner
from pipelex.system.environment import get_required_env
from pipelex.temporal.tprl_pipe.temporal_pipe_run import make_temporal_pipe_run
from pydantic import ValidationError
from typing_extensions import override

from api.error_types import ErrorType
from api.errors import raise_validation_error
from api.routes.pipelex.utils import get_current_iso_timestamp
from api.schemas.models import PipelineApiExtras

if TYPE_CHECKING:
    from mthds.models.pipe_output import VariableMultiplicity
    from mthds.models.pipeline_inputs import PipelineInputs
    from mthds.models.working_memory import WorkingMemoryAbstract
    from pipelex.core.memory.working_memory import WorkingMemory

    from api.security import RequestUser


router = APIRouter(tags=["pipeline"])


def _get_user_id(request: Request) -> str:
    """Extract the user UUID from request state (set during auth)."""
    user: RequestUser | None = getattr(request.state, "user", None)
    return user.user_id if user else "anonymous"


def _completion_signature(pipeline_run_id: str) -> str:
    """Compute the HMAC-SHA256 signature for an async pipeline completion callback.

    The signer (this server) and the verifier (your callback receiver) must
    share the same `COMPLETION_CALLBACK_SECRET`. The signature is per-run and
    the secret never travels — only the one-way hash does.
    """
    secret = get_required_env("COMPLETION_CALLBACK_SECRET")
    return hmac.new(
        secret.encode("utf-8"),
        pipeline_run_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class ApiRunner(PipelexRunner):
    """API runner that extends PipelexRunner with start_pipeline support."""

    @override
    async def start_pipeline(
        self,
        pipe_code: str | None = None,
        mthds_contents: list[str] | None = None,
        inputs: PipelineInputs | WorkingMemoryAbstract[Any] | None = None,
        output_name: str | None = None,
        output_multiplicity: VariableMultiplicity | None = None,
        dynamic_output_concept_ref: str | None = None,
        pipeline_run_id: str | None = None,
        callback_urls: list[str] | None = None,
    ) -> PipelexPipelineStartResponse:
        """Start a pipeline execution asynchronously without waiting for completion."""
        created_at = get_current_iso_timestamp()
        pipelex_inputs: PipelineInputs | WorkingMemory | None = cast("PipelineInputs | WorkingMemory | None", inputs)

        execution_config = self.execution_config or get_config().pipelex.pipeline_execution_config
        pipe_job, resolved_pipeline_run_id, _ = await pipeline_run_setup(
            execution_config=execution_config,
            library_id=self.library_id,
            library_dirs=self.library_dirs,
            pipe_code=pipe_code,
            mthds_contents=mthds_contents,
            bundle_uris=self.bundle_uris,
            inputs=pipelex_inputs,
            output_name=output_name,
            output_multiplicity=output_multiplicity,
            dynamic_output_concept_ref=dynamic_output_concept_ref,
            pipe_run_mode=self.pipe_run_mode,
            search_domain_codes=self.search_domain_codes,
            user_id=self.user_id,
            pipeline_run_id=pipeline_run_id,
        )

        delivery_assignment = DeliveryAssignment(
            storage=StorageTarget(key_prefix="results"),
            webhooks=[
                WebhookTarget(
                    url=url,
                    headers={"X-Completion-Signature": _completion_signature(resolved_pipeline_run_id)},
                )
                for url in callback_urls
            ]
            if callback_urls
            else [],
        )

        temporal_pipe_run = make_temporal_pipe_run()
        workflow_id, _handle = await temporal_pipe_run.start(
            pipe_job,
            delivery_assignment=delivery_assignment,
        )

        return PipelexPipelineStartResponse(
            pipeline_run_id=resolved_pipeline_run_id,
            created_at=created_at,
            pipeline_state=PipelineState.STARTED,
            workflow_id=workflow_id,
        )


def _decode_body(body: bytes) -> dict[str, Any]:
    """kajson-decode the body and confirm it's a dict. Raises 422 if not.

    The catch covers the kajson decode failures we've documented and
    verified empirically against the pinned kajson:
      - `UnicodeDecodeError` — body bytes are not valid UTF-8.
      - `ValueError` — `json.JSONDecodeError` (it subclasses `ValueError`).
      - `KajsonDecoderError` — kajson's named class for bad module name,
        class-not-found, enum mismatch, and pydantic validation failures.
      - bare `KeyError` / `AttributeError` / `TypeError` — protocol-shape
        leaks where kajson dereferences crafted `__class__` / `__module__`
        markers without wrapping (a `__class__` without a `__module__`, a
        non-string marker, a generic-typed class whose base fallback also
        resolves nothing). Tracked upstream so kajson eventually wraps
        them in `KajsonDecoderError`; see
        `wip/error-handling/pipelex-changes.md` #15.
    All of these are caller mistakes — the body is malformed against
    kajson's contract — so they map to a 422, not a sanitized 500. The
    scope here is one line (`kajson.loads(...)`), so catching the bare
    three cannot mask a programming bug in our code — the only source of
    those types within this try block is kajson's internal handling.
    Other failure modes (e.g. `RecursionError` from `json.JSONDecoder` on
    a deeply-nested array) are out of scope here — see Q10's resolution
    note in `TODOS.md` for the rationale and follow-up pointer.
    """
    try:
        decoded = kajson.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, KajsonDecoderError, KeyError, AttributeError, TypeError) as exc:
        raise_validation_error(
            message=f"Request body is not valid JSON: {exc!s}",
            error_type=ErrorType.INVALID_JSON,
        )
    if not isinstance(decoded, dict):
        raise_validation_error(
            message="Request body must be a JSON object",
            error_type=ErrorType.INVALID_JSON,
        )
    return cast("dict[str, Any]", decoded)


def _validate_extras(request_data: dict[str, Any]) -> PipelineApiExtras:
    """Validate API-server-only fields (pipeline_run_id, callback_urls)."""
    try:
        return PipelineApiExtras.model_validate(
            {
                "pipeline_run_id": request_data.get("pipeline_run_id"),
                "callback_urls": request_data.get("callback_urls"),
            }
        )
    except ValidationError as exc:
        raise_validation_error(
            message=str(exc),
            error_type=ErrorType.INVALID_CALLBACK_URLS,
        )


async def _parse_request(request: Request) -> tuple[PipelineRequest, PipelineApiExtras]:
    """Parse and validate the request body.

    Splits the body into:
      1. The upstream `PipelineRequest` (pipe_code, mthds_contents, inputs, …)
         decoded via `kajson` so structured inputs survive without re-parsing.
      2. `PipelineApiExtras` (pipeline_run_id, callback_urls) validated by
         Pydantic — callback_urls are checked for scheme + private/loopback
         hosts to harden against SSRF.

    Body size is capped upstream by `request_body_size_middleware`.
    """
    body = await request.body()
    request_data = _decode_body(body)
    extras = _validate_extras(request_data)
    try:
        pipeline_request = PipelineRequest.from_body(request_data)
    except (PipelineRequestError, ValidationError) as exc:
        # `from_body` rejects a body where neither `pipe_code` nor
        # `mthds_contents` is supplied (PipelineRequestError) and a body whose
        # fields fail Pydantic coercion (ValidationError) — both are caller
        # mistakes, not server faults, so they map to a 422 rather than
        # escaping to the generic-500 fallback.
        raise_validation_error(message=str(exc))
    return pipeline_request, extras


@router.post("/pipeline/execute", response_model=PipelexPipelineExecuteResponse)
async def execute(request: Request) -> JSONResponse:
    """Execute a pipeline and wait for completion.

    Pipelex domain failures propagate untouched: the global `PipelexError`
    handler in `api.exception_handlers` turns them into an RFC 7807 problem response.
    """
    pipeline_request, _extras = await _parse_request(request)
    runner = ApiRunner(user_id=_get_user_id(request))
    response = await runner.execute_pipeline(
        pipe_code=pipeline_request.pipe_code,
        mthds_contents=pipeline_request.mthds_contents,
        inputs=pipeline_request.inputs,
        output_name=pipeline_request.output_name,
        output_multiplicity=pipeline_request.output_multiplicity,
        dynamic_output_concept_ref=pipeline_request.dynamic_output_concept_ref,
    )
    return JSONResponse(
        content=response.model_dump(mode="json", serialize_as_any=True, by_alias=True),
    )


@router.post("/pipeline/start", response_model=PipelexPipelineStartResponse)
async def start(
    request: Request,
    parsed: Annotated[tuple[PipelineRequest, PipelineApiExtras], Depends(_parse_request)],
) -> PipelexPipelineStartResponse:
    """Start a pipeline execution asynchronously without waiting for completion.

    Pipelex domain failures propagate untouched: the global `PipelexError`
    handler in `api.exception_handlers` turns them into an RFC 7807 problem response.
    """
    pipeline_request, extras = parsed
    runner = ApiRunner(user_id=_get_user_id(request))
    return await runner.start_pipeline(
        pipe_code=pipeline_request.pipe_code,
        mthds_contents=pipeline_request.mthds_contents,
        inputs=pipeline_request.inputs,
        output_name=pipeline_request.output_name,
        output_multiplicity=pipeline_request.output_multiplicity,
        dynamic_output_concept_ref=pipeline_request.dynamic_output_concept_ref,
        pipeline_run_id=extras.pipeline_run_id,
        callback_urls=extras.callback_urls,
    )
