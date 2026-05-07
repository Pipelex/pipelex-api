"""Shared request/response models for API routes.

These are API-server-only models that wrap or validate fields not covered
by the upstream `mthds.client.pipeline.PipelineRequest`.
"""

from ipaddress import ip_address
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.limits import MAX_CALLBACK_URL_LEN, MAX_CALLBACK_URLS

_ALLOWED_CALLBACK_SCHEMES = frozenset({"http", "https"})


def _is_disallowed_host(host: str) -> bool:
    """True if `host` looks like a private/loopback/link-local address.

    Used to harden /pipeline/start callback_urls against SSRF — a malicious
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
    """Validates the API-server-only fields on `/pipeline/start` requests.

    The upstream `PipelineRequest` model doesn't know about these fields.
    """

    model_config = ConfigDict(extra="ignore")

    pipeline_run_id: str | None = Field(default=None, max_length=128)
    callback_urls: list[str] | None = Field(default=None, max_length=MAX_CALLBACK_URLS)

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
