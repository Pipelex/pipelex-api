from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kajson import kajson
from kajson.exceptions import KajsonDecoderError
from mthds.protocol.exceptions import PipelineRequestError
from pipelex.config import get_config
from pipelex.core.interpreter.interpreter import PipelexInterpreter
from pipelex.pipe_run.delivery_assignment import DeliveryAssignment, StorageTarget, WebhookTarget
from pipelex.pipeline.pipeline_response import PipelexRunResultExecute, PipelexRunResultStart, RunState
from pipelex.pipeline.pipeline_run_setup import pipeline_run_setup
from pipelex.pipeline.runner import PipelexMTHDSProtocol
from pipelex.pipeline.validation_report import PipelexValidationReport, build_validation_report
from pipelex.system.environment import get_required_env
from pipelex.temporal.tprl_pipe.act_dry_validate import DryValidateArg
from pipelex.temporal.tprl_pipe.dry_validate_dispatch import dispatch_dry_validate
from pipelex.temporal.tprl_pipe.temporal_pipe_run import make_temporal_pipe_run
from pydantic import ValidationError
from typing_extensions import override

from api.error_types import ErrorType
from api.errors import raise_validation_error
from api.logging_context import get_request_id
from api.routes.pipelex.utils import get_current_iso_timestamp
from api.schemas.models import PipelexApiStartRequest, PipelineApiExtras, RunRequest

if TYPE_CHECKING:
    from mthds.protocol.pipe_output import VariableMultiplicity
    from mthds.protocol.pipeline_inputs import PipelineInputs
    from mthds.protocol.working_memory import WorkingMemoryAbstract
    from pipelex.core.memory.working_memory import WorkingMemory

    from api.security import RequestUser


router = APIRouter(tags=["run"])


def _get_user_id(request: Request) -> str:
    """Extract the user UUID from request state (set during auth)."""
    user: RequestUser | None = getattr(request.state, "user", None)
    return user.user_id if user else "anonymous"


def _completion_signature(pipeline_run_id: str) -> str:
    """Compute the HMAC-SHA256 signature for an async completion callback.

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


class ApiRunner(PipelexMTHDSProtocol):
    """API runner that extends PipelexMTHDSProtocol with Temporal-backed `start` and `validate`.

    Overrides change the BACKEND (in-process vs Temporal dispatch), never the artifact
    shapes — every protocol operation answers with the same canonical models as the
    local runtime.
    """

    @override
    async def start(
        self,
        pipe_code: str | None = None,
        mthds_contents: list[str] | None = None,
        inputs: PipelineInputs | WorkingMemoryAbstract[Any] | None = None,
        output_name: str | None = None,
        output_multiplicity: VariableMultiplicity | None = None,
        dynamic_output_concept_ref: str | None = None,
        extra: dict[str, Any] | None = None,
        pipeline_run_id: str | None = None,
        callback_urls: list[str] | None = None,
        request_id: str | None = None,
    ) -> PipelexRunResultStart:
        """Start a method execution asynchronously without waiting for completion.

        `pipeline_run_id` is the client-supplied run identifier — this open-source runner
        honors it (protocol: implementations MAY decline it, but then MUST 422;
        we accept it, and `StartAck.pipeline_run_id` echoes it back as authoritative).
        `callback_urls` is THIS server's extension (completion webhooks) — the wire
        layer validates it (`PipelineApiExtras`) and passes it here by name.
        `extra` is the protocol's generic extension slot; this runner's wire
        extras are parsed by the route layer, so nothing reaches it — a
        non-empty value is an in-process misuse and is rejected. `request_id`
        is an API-layer extra threaded into `JobMetadata.request_id` for log
        correlation.
        """
        if extra:
            msg = f"ApiRunner defines no extension args beyond its named ones; got {sorted(extra)}."
            raise PipelineRequestError(msg)
        created_at = get_current_iso_timestamp()
        pipelex_inputs: PipelineInputs | WorkingMemory | None = cast("PipelineInputs | WorkingMemory | None", inputs)

        execution_config = self.execution_config or get_config().pipelex.pipeline_execution_config
        # Wire and runtime share the `pipeline_run_id` name (master D1 as
        # revised — the id rename was reversed).
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
            request_id=request_id,
        )

        delivery_assignment = DeliveryAssignment(
            storage=StorageTarget(key_prefix="results"),
            # The completion payload's wire fields (`pipeline_run_id`/`state`,
            # plus the transitional `status` alias) are written per delivery by
            # pipelex's DeliveryExecutor — they are reserved keys on
            # WebhookTarget.payload, so nothing is injected here.
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

        return PipelexRunResultStart(
            pipeline_run_id=resolved_pipeline_run_id,
            created_at=created_at,
            state=RunState.STARTED,
            workflow_id=workflow_id,
        )

    @override
    async def validate(
        self,
        mthds_contents: list[str],
        allow_signatures: bool = False,
    ) -> PipelexValidationReport:
        """Validate MTHDS bundles — protocol `validate`, Temporal-aware backend selection.

        Temporal disabled: the inherited local implementation runs in-process
        (`validate_bundle` + graph arm + `build_validation_report`, one library window).

        Temporal enabled: pure dispatch + map (D10) — the whole job (validation sweep,
        graph dry-run, and every worker-side artifact: status map, `pending_signatures`,
        `pipe_structures`) runs as ONE in-process activity on a worker; this side parses
        the blueprints (cheap, no library) and assembles the SAME canonical report via
        `build_validation_report` (D14). No API-side library acquisition.

        Either way the result is the canonical `PipelexValidationReport`: a bundle
        without a declared `main_pipe` validates fine and simply carries
        `graph_spec=None` (D2 — no precondition).
        """
        if not get_config().temporal.is_enabled:
            return await super().validate(mthds_contents=mthds_contents, allow_signatures=allow_signatures)

        # Dispatch FIRST — before any API-side parsing — so every validation failure
        # (malformed TOML, factory/wiring errors, strict-mode signature refusals)
        # surfaces through the worker's `validate_bundle` cascade with the exact same
        # categorized `ValidateBundleError` identity the direct path raises.
        dry_validate_result = await dispatch_dry_validate(DryValidateArg(mthds_contents=mthds_contents, allow_signatures=allow_signatures))

        # The bundle is known-valid now — parse the blueprints for the report. Parsing is
        # pure interpretation (no library), so the worker's single library load stays the
        # only one in the whole request.
        blueprints = [PipelexInterpreter.make_pipelex_bundle_blueprint(mthds_content=content) for content in mthds_contents]
        return build_validation_report(
            blueprints=blueprints,
            pipe_structures=dry_validate_result.pipe_structures,
            dry_run_result=dry_validate_result.dry_run_outputs,
            pending_signatures=dry_validate_result.pending_signatures,
            graph_spec=dry_validate_result.graph_spec,
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
    handler's `_pipe_code_of` / `_pipeline_run_id_of` getters see a uniform
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
      2. `PipelineApiExtras` (pipeline_run_id, callback_urls) validated by
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
    request.state.pipeline_run_id = _coerce_correlation_field(request_data.get("pipeline_run_id"))
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
    response_model=PipelexRunResultExecute,
    # The body is read through the raw Request (kajson decoding — see
    # `_parse_request`), so FastAPI cannot infer a typed body parameter;
    # document it explicitly so the committed OpenAPI artifact (and protocol
    # conformance tooling) publishes the request schema.
    openapi_extra={
        "x-mthds-protocol": True,
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": RunRequest.model_json_schema()}},
        },
    },
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
    response_model=PipelexRunResultStart,
    status_code=202,
    # Documented body = the protocol's StartRequest plus THIS server's own
    # extensions (callback_urls) — the protocol model no longer advertises
    # implementation extensions, so the server documents what it implements.
    # Raw-Request parsing prevents FastAPI from inferring it — see the
    # /execute note.
    openapi_extra={
        "x-mthds-protocol": True,
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": PipelexApiStartRequest.model_json_schema()}},
        },
    },
)
async def start(
    request: Request,
    parsed: Annotated[tuple[RunRequest, PipelineApiExtras], Depends(_parse_request)],
) -> PipelexRunResultStart:
    """Start a method asynchronously; returns its pipeline_run_id immediately (MTHDS Protocol `POST /start`).

    Answers `202 Accepted` with a `StartAck`. A client-supplied `pipeline_run_id` is
    honored (protocol D11: this runner accepts it; `StartAck.pipeline_run_id` is always
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
        pipeline_run_id=extras.pipeline_run_id,
        callback_urls=extras.callback_urls,
        request_id=get_request_id(),
    )
