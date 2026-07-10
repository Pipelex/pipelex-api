"""Envelope tests for `POST /v1/codegen` — the typed-artifact projection route.

Pins `docs/specs/pipelex-codegen.md#route-envelopes` (workspace root): the two explicit projection
axes (`kind`, `target`); a produced verdict is a 200 discriminated on `is_valid` with the stamped
artifacts + lock on the valid arm; an unknown `kind`/`target` is a request-shape 422 problem+json;
the artifacts a client writes verbatim pass the offline `codegen check` byte-for-byte.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.codegen.check import run_codegen_check
from pipelex.codegen.stamp import comment_prefix_for, parse_stamped

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from tests.unit._constants import INVALID_MAIN_PIPE_MTHDS, VALID_MTHDS


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/v1")
    register_exception_handlers(app)
    return TestClient(app)


class TestCodegenRoute:
    @pytest.mark.parametrize(
        ("target", "expected_filename"),
        [
            ("python-pydantic", "models.py"),
            ("python-structures", "structures.py"),
            ("ts-zod", "types.ts"),
        ],
    )
    def test_types_projection_returns_stamped_artifacts_and_lock(self, target: str, expected_filename: str):
        client = _build_client()
        response = client.post(
            "/v1/codegen",
            json={"files": [{"content": VALID_MTHDS}], "kind": "types", "target": target},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_valid"] is True
        assert body["kind"] == "types"
        assert body["target"] == target
        assert body["crate_fingerprint"]
        assert body["engine_version"]
        artifact_paths = [artifact["path"] for artifact in body["artifacts"]]
        assert expected_filename in artifact_paths
        # Every artifact is stamped and the stamp agrees with the envelope's fingerprint.
        first_artifact = body["artifacts"][0]
        stamped = parse_stamped(first_artifact["content"], comment_prefix=comment_prefix_for(first_artifact["path"]))
        assert stamped is not None
        assert stamped.stamp.crate_fingerprint == body["crate_fingerprint"]
        assert body["lock_filename"] == "codegen.lock"
        assert body["crate_fingerprint"] in body["lock"]

    def test_artifacts_written_verbatim_pass_the_offline_check(self, tmp_path: Path):
        # The trust-chain guarantee over HTTP: a client that writes the artifacts and the lock
        # verbatim reproduces a local projection — the offline drift check reports it current.
        client = _build_client()
        response = client.post(
            "/v1/codegen",
            json={"files": [{"content": VALID_MTHDS}], "kind": "types", "target": "python-pydantic"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        for artifact in body["artifacts"]:
            (tmp_path / artifact["path"]).write_text(artifact["content"], encoding="utf-8")
        (tmp_path / body["lock_filename"]).write_text(body["lock"], encoding="utf-8")

        drift_report = run_codegen_check(root=tmp_path)
        assert not drift_report.drifts, f"client-written artifacts must be drift-free, got: {drift_report.drifts}"

    def test_invalid_files_return_200_invalid_arm(self):
        client = _build_client()
        response = client.post(
            "/v1/codegen",
            json={"files": [{"content": INVALID_MAIN_PIPE_MTHDS}], "kind": "types", "target": "ts-zod"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_valid"] is False
        assert body["validation_errors"], "an invalid verdict must carry a non-empty validation_errors[]"
        assert "artifacts" not in body

    @pytest.mark.parametrize(
        "payload_patch",
        [
            {"kind": "does_not_exist"},
            {"target": "not-a-target"},
            {"kind": "inputs"},  # served by /build/inputs, deliberately not by this route
            {"pipe_ref": "smoke.echo"},  # types is concept-set-wide; a pipe selector is a shape error
        ],
        ids=["unknown-kind", "unknown-target", "unserved-kind", "pipe-ref-on-types"],
    )
    def test_request_shape_errors_are_422_problem_json(self, payload_patch: dict[str, str]):
        client = _build_client()
        payload: dict[str, object] = {"files": [{"content": VALID_MTHDS}], "kind": "types", "target": "ts-zod"}
        payload.update(payload_patch)
        response = client.post("/v1/codegen", json=payload)
        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    def test_method_ref_is_501_problem_json_until_registry_exists(self):
        client = _build_client()
        response = client.post("/v1/codegen", json={"method_ref": "acme/methods/x", "kind": "types", "target": "ts-zod"})
        assert response.status_code == 501, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "MethodRefNotSupported"
