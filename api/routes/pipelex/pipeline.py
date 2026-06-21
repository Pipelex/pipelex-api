from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kajson import kajson
from kajson.exceptions import KajsonDecoderError
from mthds.protocol.exceptions import PipelineRequestError
from pipelex.config import get_config
from pipelex.core.pipes.pipe_output import PipeOutput
from pipelex.hub import get_bundle_validator_registry, get_orchestrator_registry
from pipelex.pipe_run.delivery_assignment import DeliveryAssignment, StorageTarget, WebhookTarget
from pipelex.pipe_run.pipe_run_protocol import PipeRunProtocol
from pipelex.pipeline.pipeline_response import PipelexRunResultExecute, PipelexRunResultStart, RunState
from pipelex.pipeline.pipeline_run_setup import pipeline_run_setup
from pipelex.pipeline.runner import PipelexMTHDSProtocol
from pipelex.runtime_bridge.exceptions import MissingBundleValidatorError, MissingOrchestratorError
from pipelex.runtime_bridge.primitives.hydration import hydrate_working_memory
from pipelex.system.environment import get_required_env
from pydantic import ValidationError
from typing_extensions import override

from api.api_config import get_api_config, resolve_execution_mode
from api.error_types import ErrorType
from api.errors import raise_bad_request, raise_validation_error
from api.logging_context import get_request_id
from api.routes.pipelex.utils import get_current_iso_timestamp
from api.schemas.models import PipelexApiStartRequest, PipelineApiExtras, RunRequest

if TYPE_CHECKING:
    from mthds.protocol.pipe_output import VariableMultiplicity
    from mthds.protocol.pipeline_inputs import PipelineInputs
    from mthds.protocol.working_memory import WorkingMemoryAbstract
    from pipelex.base_exceptions import ErrorReport
    from pipelex.core.memory.working_memory import WorkingMemory
    from pipelex.pipe_run.pipe_job import PipeJob
    from pipelex.pipeline.validation_report import PipelexValidationReport
    from pipelex.plugins.orchestrator_registry import OrchestratorProtocol
    from pipelex.runtime_bridge.execution_mode import PipelexExecutionMode
    from pipelex.runtime_bridge.payloads import PipelexPipeRunOutput

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


def _pipe_output_from_run_output(run_output: PipelexPipeRunOutput) -> PipeOutput:
    """Rehydrate an orchestrator's JSON-safe `PipelexPipeRunOutput` into a typed `PipeOutput`.

    The `OrchestratorRegistry` answers with the JSON-safe boundary payload (the same shape
    that crosses the Temporal worker boundary), produced by `serialize_completed_output`.
    `/execute` returns the FULL output, so the synchronous path reverses that serialization
    here, restoring the rich `PipeOutput` the base `execute` then wraps in
    `PipelexRunResultExecute`:

      - `working_memory` is rebuilt via `hydrate_working_memory` — the same routine the
        Temporal workers use — so it must run while the run library (hence the scoped
        `ClassRegistry`) is still open; the base `execute` keeps it open until after the run
        returns, which is exactly this call site.
      - `graph_spec` / `tokens_usages` are validated back from their `model_dump(mode="json")`
        dumps with `strict=False`: the orchestrator dumped them in JSON mode (e.g.
        `GraphSpec.created_at` became an ISO string), and those models are `strict=True`, so a
        strict re-validation would reject the string. `strict=False` is the correct tool for
        reversing our own trusted JSON dump — it is a round-trip, not untrusted ingest.

    Routing DIRECT through this serialize→rehydrate round-trip is intentional: it keeps
    `/execute` on the SAME per-call dispatch seam as `/start` and `/validate` (no per-mode
    branch), at the cost of one in-process re-serialization of the working memory.
    """
    return PipeOutput.model_validate(
        {
            "working_memory": hydrate_working_memory(run_output.output_dict),
            "pipeline_run_id": run_output.pipeline_run_id,
            "graph_spec": run_output.graph_spec_dump,
            "graph_assembly_error": run_output.graph_assembly_error,
            "tokens_usages": run_output.tokens_usages_dump,
            "usage_assembly_error": run_output.usage_assembly_error,
        },
        strict=False,
    )


class _OrchestratorPipeRun(PipeRunProtocol):
    """Adapts a mode-selected orchestrator to the `PipeRunProtocol` the base `execute` drives.

    `ApiRunner.execute` injects one of these as `self._pipe_run` so the base `execute` retains
    ALL of its run lifecycle (library setup/teardown, tracer close, pipeline-manager cleanup,
    telemetry, error mapping) while the actual dispatch goes through the per-request
    `execution_mode`'s orchestrator instead of the boot-global pipe-run slot. The orchestrator's
    JSON-safe output is rehydrated back into the rich `PipeOutput` the base expects.
    """

    def __init__(self, *, orchestrator: OrchestratorProtocol) -> None:
        self._orchestrator = orchestrator

    @override
    async def run(self, pipe_job: PipeJob, *, delivery_assignment: DeliveryAssignment | None = None) -> PipeOutput:
        run_output = await self._orchestrator.run(pipe_job=pipe_job, delivery_assignment=delivery_assignment)
        return _pipe_output_from_run_output(run_output)


class ApiRunner(PipelexMTHDSProtocol):
    """API runner that dispatches `execute`, `start`, and `validate` through the per-call plugin registries.

    Every surface resolves the deployment's `execution_mode` (config default + optional
    per-request override) and dispatches through a per-call hub registry: `execute` runs a
    top-level pipe synchronously through the `OrchestratorRegistry` and returns the full
    output, `start` runs one asynchronously through the same registry, `validate_verdict`
    produces a validation verdict through the `BundleValidatorRegistry`. On the
    orchestrator-agnostic base that means DIRECT in-process; a `temporal_*` mode dispatches
    to a worker when `pipelex-temporal` is installed and selected. The base names no
    orchestrator and imports no orchestrator SDK; a mode with no registered arm fails loud
    with the matching `Missing*Error` (carrying the install hint). Dispatch changes the
    BACKEND, never the artifact shapes — every operation answers with the same canonical
    models as the local runtime.
    """

    @override
    async def execute(
        self,
        pipe_code: str | None = None,
        mthds_contents: list[str] | None = None,
        inputs: PipelineInputs | WorkingMemoryAbstract[Any] | None = None,
        output_name: str | None = None,
        output_multiplicity: VariableMultiplicity | None = None,
        dynamic_output_concept_ref: str | None = None,
        extra: dict[str, Any] | None = None,
        delivery_assignment: DeliveryAssignment | None = None,
        requested_execution_mode: PipelexExecutionMode | None = None,
    ) -> PipelexRunResultExecute:
        """Execute a method synchronously, dispatching by the resolved `execution_mode`.

        Symmetric with `start`: the effective `execution_mode` is resolved FIRST (config default
        + optional per-request override, so a forbidden override is refused with a 403 before any
        library load), then the run is dispatched through the hub's `OrchestratorRegistry` instead
        of the boot-global pipe-run slot. DIRECT runs in-process on this agnostic base; a
        `temporal_blocking` / `mistral_native` mode dispatches the whole job to a worker and awaits
        it. A mode with no registered orchestrator fails loud with `MissingOrchestratorError`
        (carrying the install hint).

        `/execute` is synchronous — it returns the full output — so a fire-and-forget resolution is
        meaningless and is refused with a 400 (`FIRE_AND_FORGET_NOT_SUPPORTED`): use `/start` for
        fire-and-forget. The refusal comes AFTER the override-policy check, so a forbidden override
        still 403s first.

        The orchestrator is injected as this runner's `_pipe_run` so the inherited base `execute`
        keeps the entire run lifecycle (library setup/teardown, tracer close, pipeline-manager
        cleanup, telemetry, error mapping); only the dispatch backend and the output rehydration
        (`_OrchestratorPipeRun`) change. `requested_execution_mode` is the optional per-request mode
        override (`PipelineApiExtras.execution_mode`).
        """
        # Resolve the effective execution mode FIRST — a per-request override the deployment policy
        # forbids is refused (403) here, before any library load / run registration. Mirrors start().
        execution_mode = resolve_execution_mode(requested_execution_mode, config=get_api_config())
        if execution_mode.is_fire_and_forget:
            msg = (
                "/execute is synchronous and returns the full output; a fire-and-forget execution_mode "
                "is not supported here. Use /start for fire-and-forget."
            )
            raise_bad_request(msg, error_type=ErrorType.FIRE_AND_FORGET_NOT_SUPPORTED)
        orchestrator = get_orchestrator_registry().get_optional(mode=execution_mode)
        if orchestrator is None:
            raise MissingOrchestratorError(mode=execution_mode)

        # Dispatch the run through the mode-selected orchestrator by injecting it as this runner's
        # PipeRun, then delegate to the base execute, which owns the full run lifecycle. The
        # ApiRunner is constructed per request, so mutating _pipe_run here is request-scoped.
        self._pipe_run = _OrchestratorPipeRun(orchestrator=orchestrator)
        return await super().execute(
            pipe_code=pipe_code,
            mthds_contents=mthds_contents,
            inputs=inputs,
            output_name=output_name,
            output_multiplicity=output_multiplicity,
            dynamic_output_concept_ref=dynamic_output_concept_ref,
            extra=extra,
            delivery_assignment=delivery_assignment,
        )

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
        requested_execution_mode: PipelexExecutionMode | None = None,
    ) -> PipelexRunResultStart:
        """Start a method execution asynchronously without waiting for completion.

        Dispatch is orchestrator-agnostic: the rich `PipeJob` is built locally (so
        `request_id`, `output_multiplicity`, `dynamic_output_concept_ref`, the run
        registration, and telemetry all survive) and then handed to the orchestrator the
        deployment's `execution_mode` selects, resolved through the hub's
        `OrchestratorRegistry`. The base imports no `temporalio` / orchestrator SDK; the
        Temporal fire-and-forget arm (returning a `workflow_id` immediately) is contributed
        by the `pipelex-temporal` plugin when installed. A mode with no registered
        orchestrator fails loud with `MissingOrchestratorError` (carrying the install hint).

        `pipeline_run_id` is the client-supplied run identifier — this open-source runner
        honors it (protocol: implementations MAY decline it, but then MUST 422;
        we accept it, and `StartAck.pipeline_run_id` echoes it back as authoritative).
        `callback_urls` is THIS server's extension (completion webhooks) — the wire
        layer validates it (`PipelineApiExtras`) and passes it here by name.
        `extra` is the protocol's generic extension slot; this runner's wire
        extras are parsed by the route layer, so nothing reaches it — a
        non-empty value is an in-process misuse and is rejected. `request_id`
        is an API-layer extra threaded into `JobMetadata.request_id` for log
        correlation. `requested_execution_mode` is the optional per-request mode override
        (`PipelineApiExtras.execution_mode`); it is resolved against the deployment's
        `api.toml` policy and a forbidden override is refused with a 403.
        """
        if extra:
            msg = f"ApiRunner defines no extension args beyond its named ones; got {sorted(extra)}."
            raise PipelineRequestError(msg)
        # Resolve the effective execution mode FIRST — a per-request override the deployment policy
        # forbids is refused (403) here, before any library load / run registration is done.
        execution_mode = resolve_execution_mode(requested_execution_mode, config=get_api_config())
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

        # Dispatch the locally-built job through the hub's OrchestratorRegistry under the resolved
        # mode — the same final dispatch `run_pipe_via_bridge` performs, but fed the rich PipeJob
        # instead of the lossy `PipelexPipeRunInput` (which carries no request_id /
        # output_multiplicity / dynamic_output_concept_ref and skips run registration + telemetry).
        # DIRECT runs the pipe in-process and answers with workflow_id=None; the Temporal
        # fire-and-forget arm enqueues and returns the workflow_id immediately.
        orchestrator = get_orchestrator_registry().get_optional(mode=execution_mode)
        if orchestrator is None:
            raise MissingOrchestratorError(mode=execution_mode)
        run_output = await orchestrator.run(pipe_job=pipe_job, delivery_assignment=delivery_assignment)

        return PipelexRunResultStart(
            pipeline_run_id=resolved_pipeline_run_id,
            created_at=created_at,
            state=RunState.STARTED,
            workflow_id=run_output.workflow_id,
        )

    async def validate_verdict(
        self,
        *,
        mthds_contents: list[str],
        mthds_sources: list[str] | None,
        allow_signatures: bool,
        requested_execution_mode: PipelexExecutionMode | None,
    ) -> PipelexValidationReport | ErrorReport:
        """Validate MTHDS bundles, returning the verdict as a value (the route maps it to a 200).

        Mode-aware, mirroring `start`: the effective `execution_mode` is resolved FIRST (config
        default + optional per-request override, so a forbidden override is refused with a 403
        before any library load), then dispatched through the hub's `BundleValidatorRegistry`.
        DIRECT validates in-process on this agnostic base; a `temporal_*` mode dispatches the
        whole job to a worker (`pipelex-temporal`). A mode with no registered validator fails
        loud with `MissingBundleValidatorError` (carrying the install hint).

        Returns the verdict, not a raise: the valid `PipelexValidationReport`, or the structured
        `ErrorReport` an invalid bundle produces (carrying `validation_errors`). A genuine
        no-verdict infra fault propagates to the global problem+json handler (5xx). The route
        discriminates on `isinstance(verdict, PipelexValidationReport)`.

        `mthds_sources` is the optional per-content source-threading hook: each source lands on
        the corresponding `blueprint.source`, so the structured `validation_errors` on a failure —
        and the `bundle_blueprint` on success — carry a real `source` instead of `None`. The route
        maps a length mismatch to a 422 before we get here; `None` keeps the sourceless behavior.
        `library_dirs` is host context the in-process arm needs; a dispatched arm ignores it (the
        worker loads its own library). A bundle without a declared `main_pipe` validates fine and
        simply carries `graph_spec=None` (D2 — no precondition).
        """
        # Resolve the effective mode FIRST — a per-request override the deployment policy forbids
        # is refused (403) here, before any validator dispatch / library load. Mirrors start().
        execution_mode = resolve_execution_mode(requested_execution_mode, config=get_api_config())
        validator = get_bundle_validator_registry().get_optional(mode=execution_mode)
        if validator is None:
            raise MissingBundleValidatorError(mode=execution_mode)
        library_dirs = [Path(library_dir) for library_dir in self.library_dirs] if self.library_dirs else None
        verdict = await validator.validate_bundles(
            mthds_contents=mthds_contents,
            mthds_sources=mthds_sources,
            allow_signatures=allow_signatures,
            library_dirs=library_dirs,
        )
        # The core seam types its valid arm at the protocol-level ValidationReport (a leaf type)
        # to stay import-acyclic in core; every registered validator in fact produces the canonical
        # PipelexValidationReport. Recover the precise type here — the single narrowing point — so
        # the route's `isinstance(verdict, PipelexValidationReport)` yields ErrorReport on the else arm.
        return cast("PipelexValidationReport | ErrorReport", verdict)


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
    """Validate API-server-only fields (pipeline_run_id, callback_urls, execution_mode)."""
    try:
        return PipelineApiExtras.model_validate(
            {
                "pipeline_run_id": request_data.get("pipeline_run_id"),
                "callback_urls": request_data.get("callback_urls"),
                "execution_mode": request_data.get("execution_mode"),
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

    The backend is selected by the resolved `execution_mode` (deployment default + optional
    policy-gated per-request override via the `execution_mode` extra), symmetric with `/start` —
    not by `boot_orchestrator`. A fire-and-forget mode is refused with a 400 (`/execute` is
    synchronous). Pipelex domain failures propagate untouched: the global `PipelexError` handler
    in `api.exception_handlers` turns them into an RFC 7807 problem response.
    """
    run_request, extras = await _parse_request(request)
    runner = ApiRunner(user_id=_get_user_id(request))
    response = await runner.execute(
        pipe_code=run_request.pipe_code,
        mthds_contents=run_request.mthds_contents,
        inputs=run_request.inputs,
        output_name=run_request.output_name,
        output_multiplicity=run_request.output_multiplicity,
        dynamic_output_concept_ref=run_request.dynamic_output_concept_ref,
        requested_execution_mode=extras.execution_mode,
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
    """Start a method run and return its pipeline_run_id with a 202 ack (MTHDS Protocol `POST /start`).

    Answers `202 Accepted` with a `StartAck`. A client-supplied `pipeline_run_id` is
    honored (protocol D11: this runner accepts it; `StartAck.pipeline_run_id` is always
    authoritative). Pipelex domain failures propagate untouched: the global
    `PipelexError` handler in `api.exception_handlers` turns them into an
    RFC 7807 problem response.

    Non-blocking (fire-and-forget) is a property of a **distributed** `execution_mode`: a
    Temporal fire-and-forget flavor enqueues the run and returns immediately with a
    `workflow_id`. On the orchestrator-agnostic base (`execution_mode = "direct"`, the
    default), there is no distributed backend — the run executes in-process and the request
    blocks until it completes, then answers `202` with `workflow_id: null`. The completion
    callback (`callback_urls` / storage delivery) fires on the same path in both cases.
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
        requested_execution_mode=extras.execution_mode,
    )
