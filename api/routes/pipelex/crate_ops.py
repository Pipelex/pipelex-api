"""Shared crate-resolution plumbing for the `/resolve`, `/codegen` and `/build/*` routes.

They all select a closure the same way (inline `files[]` XOR `method_ref`), resolve it through the
same engine core (`pipelex.pipeline.resolve_bundle.resolve_crate_from_contents`), and speak the same
verdict vocabulary as `POST /validate`: a produced verdict is a 200 discriminated on `is_valid`, with
the invalid arm carrying the structured `validation_errors[]` built by pipelex's one shared builder.
This module holds the pieces they share so the envelopes cannot drift.

The invalid arm here is the *crate* verdict: it deliberately omits `/validate`'s runnability facts
(`pending_signatures`, `is_runnable`) — resolution is static (no dry-run sweep, matching
`pipelex resolve`), so runnability is not part of its vocabulary. The per-pipe projections
(`/build/{inputs,output}`) ride that same static core: a template is a read of the pipe's *declared*
IO, so a valid verdict there says the closure is structurally sound, never that the pipe runs.
`/build/runner` is the exception — it needs the dry-run sweep, so it keeps `validate_bundle`.
"""

from typing import Literal, NamedTuple

from fastapi.responses import JSONResponse
from pipelex.base_exceptions import ErrorReport, ValidationErrorItem
from pipelex.core.pipes.pipe_abstract import PipeAbstract
from pipelex.hub import clear_current_library, get_current_library_id_or_none, get_library_manager, get_required_pipe
from pipelex.libraries.library_crate import LibraryCrate
from pipelex.libraries.pipe.exceptions import PipeLibraryError
from pipelex.pipeline.resolve_bundle import resolve_crate_from_contents
from pipelex.tools.typing.pydantic_utils import empty_list_factory_of
from pydantic import BaseModel, Field

from api.error_types import ErrorType
from api.errors import raise_not_implemented, raise_validation_error
from api.schemas.models import MthdsFileItem, MthdsFilesRequest


class GeneratedArtifact(BaseModel):
    """One generated file: its path relative to the client's chosen output root, and its full content."""

    path: str = Field(..., description="Artifact path, relative to the output root the client writes into.")
    content: str = Field(..., description="Complete file content, stamp header included — write verbatim.")


class CrateInvalidReport(BaseModel):
    """The 200 **invalid** arm shared by `/resolve` and `/codegen` — the crate-verdict vocabulary.

    Same discipline as `/validate`'s `InvalidReport`: an invalid library is the *successful
    product* of a diagnostic call (the request was well-formed; the library was not), so it rides
    a 200 discriminated on `is_valid`, carrying the same structured `ValidationErrorItem`s the
    local CLI and `/validate` emit for the identical failure.
    """

    is_valid: Literal[False] = False
    """Discriminant of the invalid arm (mirrors the valid arms' `Literal[True]`)."""

    validation_errors: list[ValidationErrorItem] = Field(
        default_factory=empty_list_factory_of(ValidationErrorItem),
        description="Per-error diagnostics, built by pipelex's one shared builder — non-empty on every invalid verdict.",
    )
    message: str = Field(default="MTHDS library could not be resolved", description="Human-readable summary of the verdict.")


def invalid_crate_report_response(error_report: ErrorReport) -> JSONResponse:
    """Render a produced "could not resolve" verdict as a 200 `CrateInvalidReport`.

    `exclude_none` drops each item's unset locators so the wire items match the agent CLI's
    byte-for-byte — the "one error item, two surfaces" guarantee `/validate` already keeps.
    """
    invalid_report = CrateInvalidReport(
        validation_errors=error_report.validation_errors or [],
        message=error_report.message,
    )
    return JSONResponse(content=invalid_report.model_dump(mode="json", serialize_as_any=True, by_alias=True, exclude_none=True))


def selected_files(request_data: MthdsFilesRequest) -> list[MthdsFileItem]:
    """The inline files the closure selector names, 501-ing the `method_ref` arm.

    Shared by every route on the `files[]` envelope — including `/build/runner`, which cannot use
    `resolve_requested_crate` (it needs `validate_bundle`'s dry-run sweep) but owes the caller the
    same answer on the selector it does not serve.

    Raises:
        ApiError: 501 for the `method_ref` arm until server-side registry resolution exists.
    """
    if request_data.method_ref is not None:
        raise_not_implemented(
            "method_ref resolution is not available on this server yet: no method registry is wired. Submit inline `files[]` instead.",
            error_type=ErrorType.METHOD_REF_NOT_SUPPORTED,
        )
    return request_data.files or []


def resolve_requested_crate(request_data: MthdsFilesRequest) -> LibraryCrate:
    """Resolve the request's closure selector into a normalized library crate.

    Inherits the engine core's **loaded-on-success contract**: on success the freshly opened
    library is loaded and current (so a route can read live pipes from it) and the route owns its
    teardown — call `teardown_current_library()` in a `finally`. On failure the core has already
    torn down and restored.

    Raises:
        ValidateBundleError: the produced negative verdict (route maps it to the 200 invalid arm).
        ApiError: 501 for the `method_ref` arm until server-side registry resolution exists.
    """
    files = selected_files(request_data)
    return resolve_crate_from_contents(
        mthds_contents=[item.content for item in files],
        mthds_sources=[item.source for item in files],
    )


class RequestedPipe(NamedTuple):
    """The pipe a per-pipe projection was asked for: its resolved qualified ref and the live pipe."""

    ref: str
    """The qualified `domain.pipe_code` actually projected — always qualified, whatever the request spelled."""

    pipe: PipeAbstract
    """The live pipe, read from the library `resolve_requested_crate` left loaded + current."""


def resolve_requested_pipe(crate: LibraryCrate, *, pipe_ref: str | None) -> RequestedPipe:
    """Select the pipe a per-pipe projection targets, defaulting to the closure's `main_pipe`.

    Mirrors `pipelex codegen inputs` (`inputs_cmd.py::_default_main_pipe_ref`): an omitted selector
    resolves to the single declared `main_pipe`, and **both** un-defaultable arms are rejected — a
    closure declaring none, and one declaring several across domains (ambiguous). Both are
    request-shape 422s, as is an unknown ref: nothing about the *closure* is wrong in any of them, so
    none of them is an invalid-crate verdict.

    The returned `ref` is read back off the **resolved pipe**, never echoed from the request: the
    engine's lookup accepts a bare code too (falling back across domains), so a caller that submits
    `"echo"` must still be told `"smoke.echo"` — the valid arms promise a qualified ref, and echoing
    the request back would quietly break that promise for exactly the callers who leaned on the
    fallback.

    Must be called while the library `resolve_requested_crate` opened is still loaded + current.
    """
    selector = pipe_ref or _default_main_pipe_ref(crate)
    try:
        the_pipe = get_required_pipe(pipe_code=selector)
    except PipeLibraryError as exc:
        raise_validation_error(f"Pipe '{selector}' not found in the submitted closure: {exc}")
    return RequestedPipe(ref=the_pipe.pipe_ref, pipe=the_pipe)


def _default_main_pipe_ref(crate: LibraryCrate) -> str:
    """The closure's single declared `main_pipe` (qualified), or a 422 when there is none / several."""
    candidates = [f"{domain_code}.{domain.main_pipe}" for domain_code, domain in crate.domains.items() if domain.main_pipe]
    if not candidates:
        raise_validation_error("No `pipe_ref` was given and the closure declares no `main_pipe` — name the pipe to project explicitly.")
    if len(candidates) > 1:
        joined = ", ".join(sorted(candidates))
        raise_validation_error(
            f"No `pipe_ref` was given and the closure declares several `main_pipe`s ({joined}) — name the pipe to project explicitly."
        )
    return candidates[0]


def teardown_current_library() -> None:
    """Tear down the library `resolve_requested_crate` left loaded + current (success-path cleanup)."""
    library_id = get_current_library_id_or_none()
    clear_current_library()
    if library_id is not None:
        get_library_manager().teardown(library_id=library_id)
