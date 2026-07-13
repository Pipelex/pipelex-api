"""Envelope tests for the migrated `/v1/build/*` projections.

Pins the Phase-2/3 migration: the three build routes ride the shared `files[]` XOR `method_ref`
closure selector (`MthdsFilesRequest`) plus an optional qualified `pipe_ref` that defaults to the
closure's `main_pipe`; `/build/{inputs,output}` resolve their crate **statically** (no dry-run sweep,
so no `allow_signatures`), while `/build/runner` keeps both. Verdicts stay on the `/validate`
discipline — 200 discriminated on `is_valid`, non-2xx only for no-verdict conditions.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.hub import get_library_manager
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from tests.unit._constants import (
    INVALID_MAIN_PIPE_MTHDS,
    NO_INPUTS_MTHDS,
    NO_MAIN_PIPE_MTHDS,
    SECOND_MAIN_PIPE_MTHDS,
    VALID_MTHDS,
)

BUILD_PATHS = ["/v1/build/inputs", "/v1/build/output", "/v1/build/runner"]


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/v1")
    register_exception_handlers(app)
    return TestClient(app)


class TestBuildRoutesEnvelope:
    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_pipe_ref_defaults_to_the_closures_main_pipe(self, path: str):
        # D1: the selector is optional. Omitted, it resolves to the closure's declared main_pipe, and
        # the valid arm echoes BOTH the resolved ref and (absent) the requested one, so a caller can
        # see what it was defaulted to.
        client = _build_client()
        response = client.post(path, json={"files": [{"content": VALID_MTHDS}]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_valid"] is True
        assert body["pipe_ref"] == "smoke.echo"
        assert "requested_pipe_ref" not in body, "an omitted selector must not be echoed back as if it were requested"

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_explicit_qualified_pipe_ref_is_echoed_as_requested_and_resolved(self, path: str):
        client = _build_client()
        response = client.post(path, json={"files": [{"content": VALID_MTHDS}], "pipe_ref": "smoke.echo"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pipe_ref"] == "smoke.echo"
        assert body["requested_pipe_ref"] == "smoke.echo"

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_unknown_pipe_ref_is_a_422_not_an_invalid_verdict(self, path: str):
        # Nothing about the *closure* is wrong — the caller named a pipe that isn't in it. That is a
        # no-verdict condition (422), never a 200 `is_valid: false`.
        client = _build_client()
        response = client.post(path, json={"files": [{"content": VALID_MTHDS}], "pipe_ref": "smoke.does_not_exist"})
        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_omitted_pipe_ref_on_a_closure_with_no_main_pipe_is_422(self, path: str):
        # First of the two arms `inputs_cmd.py::_default_main_pipe_ref` rejects: nothing to default to.
        client = _build_client()
        response = client.post(path, json={"files": [{"content": NO_MAIN_PIPE_MTHDS}]})
        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert "main_pipe" in response.json()["detail"]

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_omitted_pipe_ref_on_an_ambiguous_closure_is_422(self, path: str):
        # The second arm — the one the plan's D1 originally missed: two domains, two main_pipes, so the
        # default is ambiguous. Naming the pipe explicitly resolves it (asserted below).
        client = _build_client()
        payload = {"files": [{"content": VALID_MTHDS}, {"content": SECOND_MAIN_PIPE_MTHDS}]}
        response = client.post(path, json=payload)
        assert response.status_code == 422, response.text
        assert "several" in response.json()["detail"]

        named = client.post(path, json={**payload, "pipe_ref": "other.shout"})
        assert named.status_code == 200, named.text
        assert named.json()["pipe_ref"] == "other.shout"

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_invalid_closure_is_a_200_invalid_verdict(self, path: str):
        client = _build_client()
        response = client.post(path, json={"files": [{"content": INVALID_MAIN_PIPE_MTHDS}]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_valid"] is False
        assert body["validation_errors"], "an invalid verdict must carry a non-empty validation_errors[]"

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_method_ref_is_501_until_the_registry_exists(self, path: str):
        # The build routes inherit the closure selector whole — including the arm this server does not
        # serve yet. `/build/runner` reaches it through `selected_files` despite not using the crate core.
        client = _build_client()
        response = client.post(path, json={"method_ref": "acme/methods/x"})
        assert response.status_code == 501, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "MethodRefNotSupported"

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_source_labels_ride_through_to_diagnostics(self, path: str):
        # The point of the envelope migration for the build routes: a per-file `source` label reaches the
        # engine, so an invalid closure's diagnostics can name the owning file. (Before Phase 2 the build
        # routes took bare strings and could not carry one — `/build/runner` needed the widened
        # `validate_bundle(mthds_sources=...)` to get here.)
        client = _build_client()
        response = client.post(path, json={"files": [{"content": INVALID_MAIN_PIPE_MTHDS, "source": "bundles/broken.mthds"}]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_valid"] is False
        sources = [error.get("source") for error in body["validation_errors"]]
        assert "bundles/broken.mthds" in sources, f"no diagnostic carried the submitted source: {body['validation_errors']}"

    @pytest.mark.parametrize("path", ["/v1/build/inputs", "/v1/build/output"])
    def test_static_routes_reject_allow_signatures(self, path: str):
        # `allow_signatures` only ever parameterized the dry-run sweep. The static projections dropped
        # the sweep, so the flag is gone — and `MthdsFilesRequest` forbids nothing implicitly, so this
        # pins that it is genuinely absent rather than silently ignored.
        client = _build_client()
        response = client.post(path, json={"files": [{"content": VALID_MTHDS}], "allow_signatures": True})
        assert response.status_code == 200, response.text
        assert "allow_signatures" not in response.json()

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_empty_files_rejected(self, path: str):
        client = _build_client()
        response = client.post(path, json={"files": []})
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    @pytest.mark.parametrize("path", BUILD_PATHS)
    def test_routes_open_exactly_one_library_and_tear_it_down(self, path: str, mocker: MockerFixture):
        # The loaded-on-success contract: whichever core a route rides (the static
        # `resolve_requested_crate` for inputs/output, `validate_bundle` for runner), it opens exactly
        # ONE library, leaves it loaded + current, and the route owns its teardown. A second open would
        # orphan the first; a missing teardown would leak it.
        library_manager = get_library_manager()
        open_spy = mocker.spy(library_manager, "open_library")
        teardown_spy = mocker.spy(library_manager, "teardown")

        client = _build_client()
        response = client.post(path, json={"files": [{"content": VALID_MTHDS}], "pipe_ref": "smoke.echo"})

        assert response.status_code == 200, response.text
        assert open_spy.call_count == 1
        created_library_id, _ = open_spy.spy_return
        teardown_spy.assert_called_once_with(library_id=created_library_id)

    def test_inputs_json_returns_the_parsed_light_template(self):
        client = _build_client()
        response = client.post("/v1/build/inputs", json={"files": [{"content": VALID_MTHDS}]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["format"] == "json"
        assert body["explicit"] is False
        # The light shape: a Text-refining input is a bare string, not a {concept, content} envelope.
        assert isinstance(body["inputs"]["text"], str)
        assert "inputs_toml" not in body, "the format the caller did not ask for must be absent"

    def test_inputs_explicit_returns_the_ceremonial_envelope(self):
        client = _build_client()
        response = client.post("/v1/build/inputs", json={"files": [{"content": VALID_MTHDS}], "explicit": True})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["explicit"] is True
        assert set(body["inputs"]["text"]) == {"concept", "content"}

    @pytest.mark.parametrize("explicit", [False, True], ids=["light", "explicit"])
    def test_inputs_toml_returns_raw_text_not_a_parsed_dict(self, explicit: bool):
        # D3's response-shape consequence: TOML cannot ride as a parsed dict (it would lose the concept
        # comments and key order that are the reason to ask for TOML), so it rides its own string field.
        client = _build_client()
        response = client.post("/v1/build/inputs", json={"files": [{"content": VALID_MTHDS}], "format": "toml", "explicit": explicit})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["format"] == "toml"
        assert isinstance(body["inputs_toml"], str)
        assert "text" in body["inputs_toml"]
        assert "inputs" not in body, "the JSON field must be absent on a TOML request"

    @pytest.mark.parametrize("template_format", ["json", "toml"])
    def test_a_pipe_with_no_inputs_is_a_valid_verdict_with_an_empty_template(self, template_format: str):
        # NoInputsRequiredError out of the engine renderers is not a failure — it is the honest answer
        # "run this with nothing". The CLI exits 0 on it; the route answers a valid arm.
        client = _build_client()
        response = client.post("/v1/build/inputs", json={"files": [{"content": NO_INPUTS_MTHDS}], "format": template_format})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_valid"] is True
        assert "no inputs" in body["message"]
        empty = body["inputs"] if template_format == "json" else body["inputs_toml"]
        assert not empty

    @pytest.mark.parametrize(
        ("output_format", "expected_field"),
        [("schema", "output"), ("json", "output"), ("python", "output_python")],
    )
    def test_output_formats_land_on_the_field_the_format_implies(self, output_format: str, expected_field: str):
        # Regression: `format=python` used to be a hard 500 — the route fed Python source to json.loads
        # and typed the field `dict`. The structured formats stay objects; python is source text.
        client = _build_client()
        response = client.post("/v1/build/output", json={"files": [{"content": VALID_MTHDS}], "format": output_format})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["format"] == output_format
        assert expected_field in body
        if expected_field == "output":
            assert isinstance(body["output"], dict)
            assert "output_python" not in body
        else:
            assert isinstance(body["output_python"], str)
            assert "output" not in body

    def test_runner_still_sweeps_and_still_takes_allow_signatures(self, mocker: MockerFixture):
        # `/build/runner` is the one projection that keeps the dry-run (a runner script is a promise the
        # pipe runs), so it alone still accepts the flag that parameterizes that sweep.
        spy = mocker.spy(get_library_manager(), "open_library")
        client = _build_client()
        response = client.post(
            "/v1/build/runner",
            json={"files": [{"content": VALID_MTHDS}], "pipe_ref": "smoke.echo", "allow_signatures": True},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["python_code"]
        assert body["structures"]["artifacts"]
        assert spy.call_count == 1
