"""Shared request/response models for API routes.

These are API-server-only models. As of the MTHDS Protocol unification the
protocol itself defines no request model (the SDK runners take the basic args
as named parameters), so a server that wants a typed request body — like this
one — owns it. `RunRequest` / `StartRequest` are defined here, not imported.
"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Annotated, Any
from urllib.parse import urlparse

from mthds.protocol.exceptions import PipelineRequestError
from mthds.protocol.pipe_output import VariableMultiplicity
from mthds.protocol.pipeline_inputs import PipelineInputs
from mthds.protocol.working_memory import WorkingMemoryAbstract
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.functional_validators import SkipValidation

from api.limits import MAX_CALLBACK_URL_LEN, MAX_CALLBACK_URLS, MAX_MTHDS_FILE_BYTES, MAX_MTHDS_FILES_PER_REQUEST


class RunRequest(BaseModel):
    """Body of `POST /execute` — this server's typed request model.

    The MTHDS Protocol has no request model (`mthds` deleted `RunRequest`: the
    request body is just the basic args the runner already takes as named
    parameters). pipelex-api keeps a typed model so it can publish the request
    schema in its OpenAPI artifact and parse the body once.

    The declared fields are the protocol's **basic** arguments. The model is
    deliberately open (`extra="allow"`): a caller may send extra request
    properties (an extension may carry the method selector), and they are kept
    rather than silently dropped.

    Attributes:
        pipe_code: Code of the pipe to execute.
        mthds_contents: List of MTHDS bundle contents to load.
        inputs: Inputs in PipelineInputs format — Pydantic validation is skipped
            to preserve the flexible format (dicts, strings, StuffContent objects, etc.).
        output_name: Name of the output slot to write to.
        output_multiplicity: Output multiplicity setting.
        dynamic_output_concept_ref: Override for the dynamic output concept ref.
    """

    model_config = ConfigDict(extra="allow")

    pipe_code: str | None = None
    mthds_contents: list[str] | None = None
    inputs: Annotated[PipelineInputs | WorkingMemoryAbstract[Any] | None, SkipValidation] = None
    output_name: str | None = None
    output_multiplicity: VariableMultiplicity | None = None
    dynamic_output_concept_ref: str | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_request(cls, values: dict[str, Any]) -> dict[str, Any]:
        # The protocol requires at least one of pipe_code / mthds_contents. When the
        # body carries extension args (keys outside the declared fields), an extension
        # may be the method selector — the server is the source of truth, so we do not
        # over-validate.
        has_extensions = any(key not in cls.model_fields for key in values)
        if values.get("pipe_code") is None and not values.get("mthds_contents") and not has_extensions:
            msg = (
                "pipe_code and mthds_contents cannot both be empty. Either: both are provided, or if there are no mthds_contents, "
                "then pipe_code must be provided and must reference a pipe already registered in the library. "
                "If mthds_contents is provided but no pipe_code, the first content must have a main_pipe property."
            )
            raise PipelineRequestError(msg)
        return values

    @classmethod
    def from_body(cls, request_body: dict[str, Any]) -> RunRequest:
        """Build a RunRequest from the raw request-body dictionary.

        Supports both the singular `mthds_content` (legacy) and plural
        `mthds_contents`. `inputs` defaults to `{}` so a body that omits it
        still parses.
        """
        mthds_contents = request_body.get("mthds_contents")
        if mthds_contents is None:
            mthds_content = request_body.get("mthds_content")
            if mthds_content is not None:
                mthds_contents = [mthds_content]
        return cls(
            pipe_code=request_body.get("pipe_code"),
            mthds_contents=mthds_contents,
            inputs=request_body.get("inputs", {}),
            output_name=request_body.get("output_name"),
            output_multiplicity=request_body.get("output_multiplicity"),
            dynamic_output_concept_ref=request_body.get("dynamic_output_concept_ref"),
        )


class StartRequest(RunRequest):
    """Body of `POST /start` — `RunRequest` plus the optional `pipeline_run_id`.

    `pipeline_run_id` is the client-supplied run identifier; this open-source
    runner accepts it (the server-generated id echoed in the start ack is always
    authoritative). Extension args pass through `extra="allow"` exactly as on
    `RunRequest`.
    """

    pipeline_run_id: str | None = Field(default=None, max_length=128)


_ORCHESTRATION_MODE_DESCRIPTION = (
    "PIPELEX-API EXTENSION (not part of the MTHDS Protocol) — request the orchestration mode (the backend) "
    "for this run. An OPEN string token: `direct` (in-process, the base default), `temporal`, and any other "
    "plugin-provided token are accepted; an unregistered token is refused at dispatch. The delivery axis "
    "(blocking vs fire-and-forget) is endpoint-set, never requestable. Honored ONLY when the deployment sets "
    "`allow_request_orchestration_mode_override = true` in its `api.toml`; otherwise a token that differs from "
    "the deployment default is refused with a 403. Omit it to use the deployment default."
)


_ALLOWED_CALLBACK_SCHEMES = frozenset({"http", "https"})


def _is_disallowed_host(host: str) -> bool:
    """True if `host` looks like a private/loopback/link-local address.

    Used to harden /start callback_urls against SSRF — a malicious
    client could otherwise aim webhooks at internal services or cloud metadata
    endpoints (e.g. 169.254.169.254). Best-effort: hostnames that resolve to
    private addresses at fire time aren't blocked here, only literal IPs.
    """
    if not host:
        return True
    if host in {"localhost", "metadata.google.internal", "metadata"}:
        return True
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved or addr.is_unspecified


class PipelineApiExtras(BaseModel):
    """Validates the API-server-only fields on `/start` requests.

    `pipeline_run_id` is the protocol's optional start arg; `callback_urls` is
    THIS server's extension (the MTHDS Protocol defines no completion channel —
    extension args are defined and handled by the implementation that owns
    them). The upstream protocol models don't know about `callback_urls`.
    """

    model_config = ConfigDict(extra="ignore")

    pipeline_run_id: str | None = Field(default=None, max_length=128)
    callback_urls: list[str] | None = Field(default=None, max_length=MAX_CALLBACK_URLS)
    orchestration_mode: str | None = Field(default=None, description=_ORCHESTRATION_MODE_DESCRIPTION)

    @field_validator("callback_urls")
    @classmethod
    def _validate_callback_urls(cls, value: list[str] | None) -> list[str] | None:
        if not value:
            return value
        for url in value:
            if not url:
                msg = "callback_urls must be non-empty strings"
                raise ValueError(msg)
            if len(url) > MAX_CALLBACK_URL_LEN:
                msg = f"callback URL exceeds {MAX_CALLBACK_URL_LEN} chars"
                raise ValueError(msg)
            parsed = urlparse(url)
            if parsed.scheme not in _ALLOWED_CALLBACK_SCHEMES:
                msg = f"callback URL scheme must be http or https (got {parsed.scheme!r})"
                raise ValueError(msg)
            if _is_disallowed_host(parsed.hostname or ""):
                msg = f"callback URL host {parsed.hostname!r} is not allowed (private/loopback/metadata addresses are blocked)"
                raise ValueError(msg)
        return value


class PipelexApiStartRequest(StartRequest):
    """Documented body of `POST /start` — the protocol's `StartRequest` plus THIS server's extensions.

    Used only to publish the OpenAPI request schema: the protocol model no
    longer advertises implementation extensions, so this server documents the
    ones it implements itself. Wire validation happens in `PipelineApiExtras`.
    """

    callback_urls: list[str] | None = Field(
        default=None,
        description=(
            "PIPELEX-API EXTENSION (not part of the MTHDS Protocol) — completion webhooks. "
            "When the run finishes, the runner POSTs the RunResult to each URL, HMAC-SHA256-signed "
            "via the X-Completion-Signature header. http/https only; private, loopback, link-local "
            "and cloud-metadata hosts are rejected."
        ),
    )
    orchestration_mode: str | None = Field(default=None, description=_ORCHESTRATION_MODE_DESCRIPTION)


class PipelexApiExecuteRequest(RunRequest):
    """Documented body of `POST /execute` — the protocol's `RunRequest` plus THIS server's `orchestration_mode` extension.

    Used only to publish the OpenAPI request schema: `/execute` reads the body through the raw
    `Request` (kajson decoding), so FastAPI cannot infer the body type; this model documents the
    per-request `orchestration_mode` override the route actually honors (parsed by `PipelineApiExtras`).
    """

    orchestration_mode: str | None = Field(default=None, description=_ORCHESTRATION_MODE_DESCRIPTION)


class MthdsContentsRequest(BaseModel):
    """Shared base for the build/validate routes.

    Carries the bounded `mthds_contents` payload, the `allow_signatures` opt-in, and the single
    per-file size guard that every validation-performing route needs. `/validate` uses it as-is;
    the build routes subclass it to add `pipe_code` (and `/build/output` a `format`). Keeping the
    field, its public OpenAPI description, and the validator in one place stops them drifting across
    the four endpoints.
    """

    mthds_contents: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_MTHDS_FILES_PER_REQUEST,
        description="MTHDS contents to load (always an array, even for a single file).",
    )
    allow_signatures: bool = Field(
        default=False,
        description="When true, the validation sweep tolerates unimplemented pipe signatures instead of rejecting the "
        "bundle (signatures dry-run trivially by minting a mock). Defaults to false (strict).",
    )

    @field_validator("mthds_contents")
    @classmethod
    def _bound_each_file(cls, value: list[str]) -> list[str]:
        for content in value:
            if len(content.encode("utf-8")) > MAX_MTHDS_FILE_BYTES:
                msg = f"MTHDS file exceeds {MAX_MTHDS_FILE_BYTES // 1024} KiB limit"
                raise ValueError(msg)
        return value
