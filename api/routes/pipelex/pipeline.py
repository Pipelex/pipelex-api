from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kajson import kajson
from kajson.exceptions import KajsonDecoderError
from mthds.client.exceptions import PipelineRequestError
from mthds.client.pipeline import RunRequest, RunState
from pipelex.config import get_config
from pipelex.pipe_run.delivery_assignment import DeliveryAssignment, StorageTarget, WebhookTarget
from pipelex.pipeline.pipeline_response import PipelexRunResult, PipelexStartAck
from pipelex.pipeline.pipeline_run_setup import pipeline_run_setup
from pipelex.pipeline.runner import PipelexMTHDSProtocol
from pipelex.system.environment import get_required_env
from pipelex.temporal.tprl_pipe.temporal_pipe_run import make_temporal_pipe_run
from pydantic import ValidationError
from typing_extensions import override

from api.error_types import ErrorType
from api.errors import raise_validation_error
from api.logging_context import get_request_id
from api.routes.pipelex.utils import get_current_iso_timestamp
from api.schemas.models import PipelineApiExtras

if TYPE_CHECKING:
    from mthds.models.pipe_output import VariableMultiplicity
    from mthds.models.pipeline_inputs import PipelineInputs
    from mthds.models.working_memory import WorkingMemoryAbstract
    from pipelex.core.memory.working_memory import WorkingMemory

    from api.security import RequestUser


router = APIRouter(tags=["run"])


def _get_user_id(request: Request) -> str:
    """Extract the user UUID from request state (set during auth)."""
    user: RequestUser | None = getattr(request.state, "user", None)
    return user.user_id if user else "anonymous"


def _completion_signature(run_id: str) -> str:
    """Compute the HMAC-SHA256 signature for an async completion callback.

    The signer (this server) and the verifier (your callback receiver) must
    share the same `COMPLETION_CALLBACK_SECRET`. The signature is per-run and
    the secret never travels — only the one-way hash does.
    """
    secret = get_required_env("COMPLETION_CALLBACK_SECRET")
    return hmac.new(
        secret.encode("utf-8"),
        run_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class ApiRunner(PipelexMTHDSProtocol):
    """API runner that extends PipelexMTHDSProtocol with async `start` support (Temporal dispatch)."""

    @override
    async def start(
        self,
        pipe_code: str | None = None,
        mthds_contents: list[str] | None = None,
        inputs: PipelineInputs | WorkingMemoryAbstract[Any] | None = None,
        output_name: str | None = None,
        output_multiplicity: VariableMultiplicity | None = None,
        dynamic_output_concept_ref: str | None = None,
        run_id: str | None = None,
        callback_urls: list[str] | None = None,
        method_id: str | None = None,
        request_id: str | None = None,
    ) -> PipelexStartAck:
        """Start a method execution asynchronously without waiting for completion.

        `run_id` is the client-supplied run identifier — this open-source runner
        honors it (protocol: implementations MAY decline it, but then MUST 422;
        we accept it, and `StartAck.run_id` echoes it back as authoritative).
        `method_id` is a hosted-API extension this runner does not implement; it
        is accepted for protocol-signature compatibility and ignored (the wire
        layer never forwards it). `request_id` is an API-layer extra threaded
        into `JobMetadata.request_id` for log correlation.
        """
        _ = method_id  # hosted extension — not implemented by the OSS runner
        created_at = get_current_iso_timestamp()
        pipelex_inputs: PipelineInputs | WorkingMemory | None = cast("PipelineInputs | WorkingMemory | None", inputs)

        execution_config = self.execution_config or get_config().pipelex.pipeline_execution_config
        # The pipelex runtime internals keep the `pipeline_run_id` parameter
        # name (master D1) — only the wire renames to `run_id`.
        pipe_job, resolved_run_id, _ = await pipeline_run_setup(
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
            pipeline_run_id=run_id,
            request_id=request_id,
        )

        delivery_assignment = DeliveryAssignment(
            storage=StorageTarget(key_prefix="results"),
            # The completion payload's wire fields (`run_id`/`state`, plus the
            # transitional `pipeline_run_id`/`status` aliases) are written per
            # delivery by pipelex's DeliveryExecutor — they are reserved keys
            # on WebhookTarget.payload, so nothing is injected here.
            webhooks=[
                WebhookTarget(
                    url=url,
                    headers={"X-Completion-Signature": _completion_signature(resolved_run_id)},
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

        return PipelexStartAck(
            run_id=resolved_run_id,
            created_at=created_at,
            state=RunState.STARTED,
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
        `wip/pipelex-changes.md` #15.
      - `RecursionError` — a deeply-nested JSON array or object exhausts
        the interpreter's recursion budget inside the JSON parser. Still
        a caller-controllable input shape, so it maps to 422 alongside
        the rest rather than escaping to a sanitized 500.
    All of these are caller mistakes — the body is malformed against
    kajson's contract — so they map to a 422, not a sanitized 500. The
    scope here is one line (`kajson.loads(...)`), so catching the bare
    three cannot mask a programming bug in our code — the only source of
    those types within this try block is kajson's internal handling.
    """
    try:
        decoded = kajson.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, KajsonDecoderError, KeyError, AttributeError, TypeError, RecursionError) as exc:
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
    """Validate API-server-only fields (run_id, callback_urls)."""
    try:
        return PipelineApiExtras.model_validate(
            {
                "run_id": request_data.get("run_id"),
                "callback_urls": request_data.get("callback_urls"),
            }
        )
    except ValidationError as exc:
        raise_validation_error(
            message=str(exc),
            error_type=ErrorType.INVALID_CALLBACK_URLS,
        )


# Per-field bound applied at the request.state binding site so an oversized
# caller-supplied `pipe_code` cannot blow up downstream log-line size.
# `RunRequest.pipe_code` carries no Pydantic `max_length`; this is the
# narrow cap that protects the structured error log without changing the
# upstream type contract. 256 covers any realistic pipe code (kebab-case
# identifier, typically tens of chars) with headroom.
_MAX_CORRELATION_FIELD_LEN = 256


def _coerce_correlation_field(value: Any) -> str | None:
    """Normalize a body-derived correlation identifier for `request.state` binding.

    Returns `None` when the value is missing, empty, or non-string — so the
    handler's `_pipe_code_of` / `_run_id_of` getters see a uniform
    `None` and `emit_error_log` drops the field rather than rendering a bare
    `pipe_code=` token (the empty-string case would otherwise pass the
    `is not None` filter in `emit_error_log` and look like a logfmt parse
    error to downstream sinks). Truncates oversized strings to
    `_MAX_CORRELATION_FIELD_LEN` so a caller cannot inflate every error log
    line for the request by sending a megabyte-long pipe_code.
    """
    if not isinstance(value, str) or not value:
        return None
    return value[:_MAX_CORRELATION_FIELD_LEN]


async def _parse_request(request: Request) -> tuple[RunRequest, PipelineApiExtras]:
    """Parse and validate the request body.

    Splits the body into:
      1. The upstream `RunRequest` (pipe_code, mthds_contents, inputs, …)
         decoded via `kajson` so structured inputs survive without re-parsing.
      2. `PipelineApiExtras` (run_id, callback_urls) validated by
         Pydantic — callback_urls are checked for scheme + private/loopback
         hosts to harden against SSRF.

    Body size is capped upstream by `request_body_size_middleware`.
    """
    body = await request.body()
    request_data = _decode_body(body)
    # Bind body-derived correlation identifiers onto `request.state` as soon as
    # the raw dict is in hand — before `_validate_extras` or `from_body`, so a
    # later validation failure (a rejected callback URL, a Pydantic coercion
    # error on a sibling field) still rides the identifiers the caller named.
    # `_coerce_correlation_field` normalizes empty / non-string / oversized.
    # Mirrors the `_set_request_user` pattern in `api.security` (binding the
    # earliest known identity onto the request).
    request.state.pipe_code = _coerce_correlation_field(request_data.get("pipe_code"))
    request.state.run_id = _coerce_correlation_field(request_data.get("run_id"))
    extras = _validate_extras(request_data)
    try:
        run_request = RunRequest.from_body(request_data)
    except (PipelineRequestError, ValidationError) as exc:
        # `from_body` rejects a body where neither `pipe_code` nor
        # `mthds_contents` is supplied (PipelineRequestError) and a body whose
        # fields fail Pydantic coercion (ValidationError) — both are caller
        # mistakes, not server faults, so they map to a 422 rather than
        # escaping to the generic-500 fallback.
        raise_validation_error(message=str(exc))
    return run_request, extras


@router.post(
    "/execute",
    response_model=PipelexRunResult,
    openapi_extra={"x-mthds-protocol": True},
)
async def execute(request: Request) -> JSONResponse:
    """Execute a method synchronously and return its full output (MTHDS Protocol `POST /execute`).

    Pipelex domain failures propagate untouched: the global `PipelexError`
    handler in `api.exception_handlers` turns them into an RFC 7807 problem response.
    """
    run_request, _extras = await _parse_request(request)
    runner = ApiRunner(user_id=_get_user_id(request))
    response = await runner.execute(
        pipe_code=run_request.pipe_code,
        mthds_contents=run_request.mthds_contents,
        inputs=run_request.inputs,
        output_name=run_request.output_name,
        output_multiplicity=run_request.output_multiplicity,
        dynamic_output_concept_ref=run_request.dynamic_output_concept_ref,
    )
    return JSONResponse(
        content=response.model_dump(mode="json", serialize_as_any=True, by_alias=True),
    )


@router.post(
    "/start",
    response_model=PipelexStartAck,
    status_code=202,
    openapi_extra={"x-mthds-protocol": True},
)
async def start(
    request: Request,
    parsed: Annotated[tuple[RunRequest, PipelineApiExtras], Depends(_parse_request)],
) -> PipelexStartAck:
    """Start a method asynchronously; returns its run_id immediately (MTHDS Protocol `POST /start`).

    Answers `202 Accepted` with a `StartAck`. A client-supplied `run_id` is
    honored (protocol D11: this runner accepts it; `StartAck.run_id` is always
    authoritative). Pipelex domain failures propagate untouched: the global
    `PipelexError` handler in `api.exception_handlers` turns them into an
    RFC 7807 problem response.
    """
    run_request, extras = parsed
    runner = ApiRunner(user_id=_get_user_id(request))
    return await runner.start(
        pipe_code=run_request.pipe_code,
        mthds_contents=run_request.mthds_contents,
        inputs=run_request.inputs,
        output_name=run_request.output_name,
        output_multiplicity=run_request.output_multiplicity,
        dynamic_output_concept_ref=run_request.dynamic_output_concept_ref,
        run_id=extras.run_id,
        callback_urls=extras.callback_urls,
        request_id=get_request_id(),
    )
