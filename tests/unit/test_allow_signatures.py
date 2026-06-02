"""`allow_signatures` opt-in across the build/validate routes.

A bundle containing an unimplemented `PipeSignature` is rejected by default (strict mode) and
accepted when the request opts in via `allow_signatures: true`. The strict rejection is a 422 RFC
7807 `application/problem+json` — a caller-fixable `INPUT` error — surfaced as `SignaturesNotAllowedError`
on `/build/runner` (which validates the pipes directly via `BundleValidator.validate_pipes`) and as
`ValidateBundleError` on the routes that wrap validation through `validate_bundle`.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from tests.unit._constants import SIGNATURE_MTHDS


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    register_exception_handlers(app)
    return TestClient(app)


class TestAllowSignatures:
    @pytest.mark.parametrize(
        ("path", "payload", "expected_error_type"),
        [
            ("/api/v1/validate", {"mthds_contents": [SIGNATURE_MTHDS]}, "ValidateBundleError"),
            ("/api/v1/build/runner", {"mthds_contents": [SIGNATURE_MTHDS], "pipe_code": "caller_seq"}, "SignaturesNotAllowedError"),
            ("/api/v1/build/inputs", {"mthds_contents": [SIGNATURE_MTHDS], "pipe_code": "caller_seq"}, "ValidateBundleError"),
            ("/api/v1/build/output", {"mthds_contents": [SIGNATURE_MTHDS], "pipe_code": "caller_seq"}, "ValidateBundleError"),
        ],
    )
    def test_signatures_rejected_by_default(self, path: str, payload: dict[str, object], expected_error_type: str):
        client = _build_client()
        response = client.post(path, json=payload)
        # Strict mode (the default): an unimplemented signature is a caller-fixable input error, so it
        # renders as a 422 RFC 7807 — not a 500. `SignaturesNotAllowedError` carries
        # `error_domain = INPUT` (class-level), which is what maps it to 422 on the runner route.
        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["error_type"] == expected_error_type
        assert body["error_domain"] == "input"
        # The message names the offending construct so the caller knows what to fix (not a
        # tautological "allow" OR "signature" — assert the actual subject of the rejection).
        assert "signature" in body["detail"].lower()

    @pytest.mark.parametrize(
        ("path", "payload"),
        [
            ("/api/v1/validate", {"mthds_contents": [SIGNATURE_MTHDS], "allow_signatures": True}),
            ("/api/v1/build/runner", {"mthds_contents": [SIGNATURE_MTHDS], "pipe_code": "caller_seq", "allow_signatures": True}),
            ("/api/v1/build/inputs", {"mthds_contents": [SIGNATURE_MTHDS], "pipe_code": "caller_seq", "allow_signatures": True}),
            ("/api/v1/build/output", {"mthds_contents": [SIGNATURE_MTHDS], "pipe_code": "caller_seq", "allow_signatures": True}),
        ],
    )
    def test_signatures_accepted_when_opted_in(self, path: str, payload: dict[str, object]):
        client = _build_client()
        response = client.post(path, json=payload)
        # allow_signatures=true: the signature dry-runs trivially (mock mint), so validation passes
        # and the route returns its normal 200 payload.
        assert response.status_code == 200, response.text

    def test_build_runner_generates_code_for_bundle_with_signatures_when_opted_in(self):
        # The most concrete proof the flag threads through to the runner build: with allow_signatures
        # the dry-run sweep tolerates the signature, the library stays open, and runner code is
        # generated for the caller pipe.
        client = _build_client()
        response = client.post(
            "/api/v1/build/runner",
            json={"mthds_contents": [SIGNATURE_MTHDS], "pipe_code": "caller_seq", "allow_signatures": True},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["pipe_code"] == "caller_seq"
        assert body["python_code"]
