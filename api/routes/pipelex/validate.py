from typing import Annotated, Literal, Self, Union

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pipelex.base_exceptions import ErrorReport, ValidationErrorItem
from pipelex.pipeline.validation_render import format_validate_markdown, render_invalid_validation_markdown
from pipelex.pipeline.validation_report import PipelexValidationReport
from pipelex.tools.typing.pydantic_utils import empty_list_factory_of
from pipelex.types import StrEnum
from pydantic import BaseModel, Field, model_validator

from api.exception_handlers import problem_response_from_error_report
from api.routes.pipelex.pipeline import ApiRunner
from api.schemas.models import MthdsContentsRequest

router = APIRouter(tags=["validate"])


class RenderFormat(StrEnum):
    """The closed set of server-side **supported** presentation formats for `/validate`.

    This is the supported-set vocabulary the route resolves request `render` tokens against —
    NOT the request-body type (the request stays `list[str]` so an unknown token is lenient-ignored,
    not 422'd). A Pipelex-API presentation concern (D-D): never the neutral protocol body.
    """

    MARKDOWN = "markdown"


def _resolve_render_formats(render: list[str]) -> set[RenderFormat]:
    """Resolve raw `render` tokens to the supported `RenderFormat` set (deduped, order-insensitive).

    Unknown/unsupported tokens are silently dropped (lenient-ignore, per-token): `render` is a
    presentation hint, not part of the verdict contract, so a stale view token never fails the call.
    """
    supported_values = {render_format.value for render_format in RenderFormat}
    return {RenderFormat(token) for token in render if token in supported_values}


class ValidateRequest(MthdsContentsRequest):
    """The shared `mthds_contents` + `allow_signatures` payload, plus optional per-file sources.

    `mthds_sources`, when provided, pairs each `mthds_contents[i]` with a logical source (e.g. the
    file's path relative to the submitted directory). The runner threads it onto
    `blueprint.source`, so the structured `validation_errors` on a 200 `InvalidReport` carry a real
    `source` the client maps back to the owning file — without it the in-memory load path leaves
    `source` null and cross-file diagnostics misfire. Omit it and behavior is unchanged.
    """

    mthds_sources: list[str] | None = Field(
        default=None,
        description=(
            "Optional per-file sources, parallel to `mthds_contents`. When provided, each entry is threaded "
            "onto the corresponding bundle's `source` so server-side validation errors carry a `source` pointing "
            "at the owning file. Must match `mthds_contents` in length when present."
        ),
    )
    render: list[str] = Field(
        default_factory=list,
        description=(
            "Opt-in Pipelex-API presentation extra: view formats to render server-side. A supported token "
            "(`markdown`) adds a `rendered_<format>` field (e.g. `rendered_markdown`) to the 200 verdict, on both "
            "the valid and invalid arms. Unknown/unsupported tokens are silently ignored (presentation hint, not "
            "part of the verdict contract); the default empty list renders nothing and the response is unchanged."
        ),
    )
    orchestration_mode: str | None = Field(
        default=None,
        description=(
            "Optional per-request orchestration-mode (backend) override for the validation dispatch (same plumbing as "
            "`/start`). An OPEN string token: `direct` validates in-process; a `temporal` mode dispatches the whole "
            "job to a worker; any plugin-provided token is accepted and an unregistered one is refused at dispatch. "
            "Honored only when the deployment sets `allow_request_orchestration_mode_override = true` in its `api.toml`; "
            "otherwise a token that differs from the deployment default is refused with a 403. Omitted → the default."
        ),
    )

    @model_validator(mode="after")
    def _sources_match_contents(self) -> Self:
        # A caller-supplied length mismatch is a request-shape bug → caught here as a 422.
        # Without this guard it reaches the runtime's `validate_bundle`, which treats the
        # mismatch as an internal host error (500) — the wrong status for caller input.
        if self.mthds_sources is not None and len(self.mthds_sources) != len(self.mthds_contents):
            msg = "mthds_sources, when provided, must be a per-item source list matching mthds_contents in length"
            raise ValueError(msg)
        return self


class ValidReport(PipelexValidationReport):
    """The 200 **valid** arm: the canonical `PipelexValidationReport` plus this server's wire-only extras.

    The report fields are inherited — typed models, identical to what the local runtime
    returns for the same bundle, with `is_valid: Literal[True]` (from the report) as the union
    discriminant. The extras exist for HTTP clients only (the webapp reads back `mthds_contents`);
    they are NOT part of the canonical report and no in-process consumer should depend on them.
    """

    mthds_contents: list[str] = Field(..., description="The MTHDS contents that were validated (echo of the request)")
    message: str = Field(default="MTHDS content validated successfully", description="Status message")
    rendered_markdown: str | None = Field(
        default=None,
        description=(
            "Opt-in Pipelex-API presentation extra (D-D): a server-rendered Markdown view of the valid verdict, "
            "present only when the request's `render` includes `markdown`. Absent by default — the structured fields "
            "remain the contract; this is the view."
        ),
    )


class InvalidReport(BaseModel):
    """The 200 **invalid** arm: a produced "invalid" verdict, discriminated on `is_valid: false`.

    An invalid bundle is the *successful product* of a diagnostic call, not a transport failure
    (the request was well-formed; the bundle was not), so it rides a **200** — the global
    `problem+json` 422/5xx is reserved for the no-verdict conditions (malformed request body,
    `mthds_sources` length mismatch, auth, server fault). The structural artifacts
    (`bundle_blueprint`, `pipe_io_contracts`, `graph_spec`, `validated_pipes`) do not exist when
    load/parse/wiring failed, so this arm omits them and carries only the per-error diagnostics
    plus the runnability facts.
    """

    is_valid: Literal[False] = False
    """Discriminant of the invalid arm (mirrors `ValidReport`/`PipelexValidationReport`'s `Literal[True]`)."""

    validation_errors: list[ValidationErrorItem] = Field(
        default_factory=empty_list_factory_of(ValidationErrorItem),
        description="Per-error diagnostics, built by pipelex's one shared builder — non-empty on every invalid verdict.",
    )
    pending_signatures: list[str] = Field(
        default_factory=list,
        description="Best-effort outstanding signatures; empty on the invalid arm since no library was assembled.",
    )
    is_runnable: Literal[False] = False
    """An invalid bundle is never runnable."""

    message: str = Field(default="MTHDS validation found errors", description="Human-readable summary of the verdict.")
    rendered_markdown: str | None = Field(
        default=None,
        description=(
            "Opt-in Pipelex-API presentation extra (D-D): a server-rendered Markdown view of the invalid verdict's "
            "`validation_errors`, present only when the request's `render` includes `markdown`. Absent by default."
        ),
    )


# Discriminated 200 response union (D-C): a consumer pattern-matches the one mandatory `is_valid`
# field to learn the verdict, without inspecting a status code or catching an exception body.
ValidationResponse = Annotated[Union[ValidReport, InvalidReport], Field(discriminator="is_valid")]


@router.post("/validate", response_model=ValidationResponse, openapi_extra={"x-mthds-protocol": True})
async def validate_mthds(request: Request, request_data: ValidateRequest) -> JSONResponse:
    """Validate MTHDS content by parsing, loading, and dry-running pipes (MTHDS Protocol `POST /validate`).

    `/validate` is a **diagnostic endpoint**: any verdict the validator can produce — valid,
    invalid, or valid-but-not-runnable — rides a **200** discriminated in the body on `is_valid`.
    Non-2xx is reserved for the cases where *no verdict could be produced*.

    Response contract:

    - **Valid verdict (200, `is_valid: true`):** the `ValidReport` arm — the canonical report
      (primary `bundle_blueprint`, `pipe_io_contracts` keyed by namespaced `pipe_ref`, per-pipe
      `validated_pipes` sweep outcomes, `pending_signatures` + `is_runnable` runnability verdict,
      best-effort `graph_spec`) plus the wire extras (`mthds_contents` echo, `message`). A bundle
      that declares no `main_pipe` validates fine and carries `graph_spec=null`. Pending
      signatures are reported as `pending_signatures` + `is_runnable: false`, never as an error.
    - **Invalid verdict (200, `is_valid: false`):** the `InvalidReport` arm — `validation_errors[]`
      (the structured per-error diagnostics, built by pipelex's one shared builder, incl. the
      `dry_run` residual item) + `message`, with the structural artifacts absent. The runner
      returns this as a value (`ErrorReport` with `validation_errors`) regardless of backend — the
      in-process arm from the bundle's `ValidateBundleError`, the dispatched arm recovered from the
      worker — so the route maps it to a 200 by matching validation diagnostics, never by catching an
      exception. Returned `ErrorReport`s without validation diagnostics are backend/config/runtime
      faults and keep the global RFC 7807 problem response path.
    - **No verdict (non-2xx):** a malformed request body or an `mthds_sources` length mismatch is a
      request-shape **422**; a forbidden `orchestration_mode` override is a **403**; a host-wiring
      programmer error or a genuine orchestrator fault is a **5xx**; auth is **401/403**. All are
      RFC 7807 `application/problem+json` rendered by the global handler in
      `api.exception_handlers` — routes never shape them.
    """
    # Opt-in presentation formats (D-D): resolved once, threaded into both 200 arms. Empty by
    # default → no `rendered_*` field, response byte-identical to the no-`render` request.
    requested_formats = _resolve_render_formats(request_data.render)
    # Verdict-as-value: the runner resolves the orchestration mode and dispatches through the bundle
    # validator registry, returning either a validation verdict or a classified fault report.
    # Only `ErrorReport`s with validation diagnostics are invalid-bundle verdicts (→ 200
    # InvalidReport); backend/config/runtime reports keep the global problem+json mapping.
    verdict = await ApiRunner().validate_verdict(
        mthds_contents=request_data.mthds_contents,
        mthds_sources=request_data.mthds_sources,
        allow_signatures=request_data.allow_signatures,
        requested_orchestration_mode=request_data.orchestration_mode,
    )
    if not isinstance(verdict, PipelexValidationReport):
        if verdict.validation_errors:
            return _invalid_report_response(verdict, requested_formats=requested_formats)
        return problem_response_from_error_report(verdict, request=request)
    report = verdict

    # Splat the report's own field/value pairs so a future canonical field rides the wire
    # automatically — the wrapper never enumerates (and silently drops) report fields. `is_valid`
    # rides through from the report as the valid-arm discriminant (True).
    response_data = ValidReport.model_validate({**dict(report), "mthds_contents": request_data.mthds_contents})
    content = response_data.model_dump(mode="json", serialize_as_any=True, by_alias=True)
    # `rendered_markdown` is a presentation extra (D-D), not part of the report: attach it only when
    # `markdown` was requested, else pop it so the response stays byte-identical to a no-`render` call
    # (the valid arm is dumped without `exclude_none`, so the default `null` would otherwise linger).
    # Rendered from the canonical report dict — the same shape the local agent CLI feeds the renderer,
    # so the valid-arm Markdown shares one source of truth and cannot drift in format/structure.
    if RenderFormat.MARKDOWN in requested_formats:
        content["rendered_markdown"] = format_validate_markdown(report.model_dump(mode="json"))
    else:
        content.pop("rendered_markdown", None)
    return JSONResponse(content=content)


def _invalid_report_response(error_report: ErrorReport, *, requested_formats: set[RenderFormat]) -> JSONResponse:
    """Render a produced "invalid" verdict as a 200 `InvalidReport` (D-A / D-C / D-D).

    The `validation_errors[]` come straight from pipelex's one shared builder via
    `ValidateBundleError.to_error_report()`, so the hosted invalid arm carries the same typed
    items the agent CLI emits (including the `dry_run` residual item — the structured-info
    invariant guarantees this list is non-empty on every invalid verdict that reaches the wire,
    since the empty-`mthds_contents` edge case is a request-shape 422 via `min_length=1`).
    `message` is the caller-facing summary the error report already carries.
    """
    invalid_report = InvalidReport(
        validation_errors=error_report.validation_errors or [],
        message=error_report.message,
    )
    # `exclude_none` drops each item's unset locators, so the wire items match the agent CLI's
    # `extract_validation_errors` byte-for-byte (it dumps items the same way) — the "one error item,
    # two surfaces" guarantee. The invalid arm's own fields are all non-None, so none are lost; and
    # `rendered_markdown` stays absent here unless explicitly requested below.
    content = invalid_report.model_dump(mode="json", serialize_as_any=True, by_alias=True, exclude_none=True)
    # Opt-in presentation extra (D-D): a faithful render of the structured `validation_errors`,
    # attached only when `markdown` was requested. Built from the just-dumped content so the renderer
    # reads the same items the wire carries.
    if RenderFormat.MARKDOWN in requested_formats:
        content["rendered_markdown"] = render_invalid_validation_markdown(content)
    return JSONResponse(content=content)
