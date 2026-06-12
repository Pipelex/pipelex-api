"""`/validate` envelope contract — the canonical report + wire extras, on both backends.

Phase 2 of the MTHDS Protocol surface alignment: `/validate` routes through
`ApiRunner.validate`, so the HTTP envelope is the canonical `PipelexValidationReport`
(`bundle_blueprint`, `pipe_structures` keyed by namespaced `pipe_ref`, `validated_pipes`,
`pending_signatures` + `is_runnable`, best-effort `graph_spec`) plus this server's
wire-only extras (`mthds_contents` echo, `success`, `message`). The direct backend runs the
real in-process validation; the Temporal backend is exercised by faking the dispatch with a
realistic worker result and asserting the pure dispatch + map contract (D10): same canonical
envelope, zero API-side library acquisition.

Includes the D2 regression pin: a bundle that declares no `main_pipe` validates with 200 and
`graph_spec=null` on BOTH backends — the former 422 precondition is deleted.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipelex.hub import get_library_manager
from pipelex.pipeline.bundle_validator import DryRunOutput, DryRunStatus
from pipelex.pipeline.pipe_structures import IOMultiplicity, PipeInputContract, PipeIOContract, PipeOutputContract
from pipelex.temporal.tprl_pipe.act_dry_validate import DryValidateResult
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from tests.unit._constants import NO_MAIN_PIPE_MTHDS, SIGNATURE_MTHDS, VALID_MTHDS


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/v1")
    register_exception_handlers(app)
    return TestClient(app)


def _enable_fake_temporal_backend(mocker: MockerFixture, worker_result: DryValidateResult) -> Any:
    """Flip ApiRunner.validate onto its Temporal arm with a faked worker round-trip."""
    fake_config = mocker.MagicMock()
    fake_config.temporal.is_enabled = True
    mocker.patch("api.routes.pipelex.pipeline.get_config", return_value=fake_config)
    return mocker.patch(
        "api.routes.pipelex.pipeline.dispatch_dry_validate",
        new=mocker.AsyncMock(return_value=worker_result),
    )


class TestValidateEnvelope:
    def test_direct_success_envelope_is_canonical_report_plus_wire_extras(self):
        client = _build_client()
        response = client.post("/v1/validate", json={"mthds_contents": [VALID_MTHDS]})
        assert response.status_code == 200, response.text
        body = response.json()

        # Canonical report (D1/D3/D7/D8): primary blueprint under `bundle_blueprint` —
        # the pipelex_-prefixed field and the old local `blueprint` dump are both gone.
        assert "pipelex_bundle_blueprint" not in body
        assert "blueprint" not in body
        assert body["bundle_blueprint"]["domain"] == "smoke"
        assert body["bundle_blueprint"]["main_pipe"] == "echo"

        # `pipe_structures` keyed by namespaced pipe_ref with typed IO contracts.
        structure = body["pipe_structures"]["smoke.echo"]
        assert structure["output"]["multiplicity"] == "single"
        assert structure["output"]["concept_code"]
        assert "text" in structure["inputs"]
        assert structure["inputs"]["text"]["concept_code"]

        # Per-pipe sweep outcomes, entries keyed `pipe_ref` (D7) — never `pipe_code`.
        assert body["validated_pipes"] == [{"pipe_ref": "smoke.echo", "status": DryRunStatus.SUCCESS}]

        # Runnability verdict + best-effort graph (main_pipe declared → graph produced).
        assert body["pending_signatures"] == []
        assert body["is_runnable"] is True
        assert body["graph_spec"] is not None

        # Wire extras the webapp depends on.
        assert body["mthds_contents"] == [VALID_MTHDS]
        assert body["success"] is True
        assert body["message"]

    def test_direct_lenient_signatures_report_verdict(self):
        client = _build_client()
        response = client.post("/v1/validate", json={"mthds_contents": [SIGNATURE_MTHDS], "allow_signatures": True})
        assert response.status_code == 200, response.text
        body = response.json()
        # The runnability verdict crosses the HTTP surface at last (F5 closed).
        assert body["pending_signatures"] == ["sig_api.summary_sig"]
        assert body["is_runnable"] is False
        # The signature placeholder is a first-class pipe on every artifact.
        assert set(body["pipe_structures"]) == {"sig_api.caller_seq", "sig_api.summary_sig"}
        validated_refs = {entry["pipe_ref"] for entry in body["validated_pipes"]}
        assert validated_refs == {"sig_api.caller_seq", "sig_api.summary_sig"}

    def test_direct_no_main_pipe_returns_200_without_graph(self):
        # D2 regression pin (direct backend): this used to be a 422 route precondition.
        client = _build_client()
        response = client.post("/v1/validate", json={"mthds_contents": [NO_MAIN_PIPE_MTHDS]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["graph_spec"] is None
        assert body["bundle_blueprint"]["domain"] == "nomain"
        assert body["pipe_structures"]["nomain.echo"]["output"]["multiplicity"] == "single"
        assert body["validated_pipes"] == [{"pipe_ref": "nomain.echo", "status": DryRunStatus.SUCCESS}]
        assert body["is_runnable"] is True
        assert body["success"] is True

    def test_temporal_backend_is_pure_dispatch_and_map(self, mocker: MockerFixture):
        # D10: everything worker-computed rides DryValidateResult; the API side parses
        # blueprints and assembles the SAME canonical envelope — no library acquisition.
        # Uses the no-main_pipe bundle so this also pins D2 on the Temporal arm (the old
        # route 422'd AFTER the dispatch).
        worker_result = DryValidateResult(
            dry_run_outputs={"nomain.echo": DryRunOutput(pipe_code="echo", pipe_ref="nomain.echo", status=DryRunStatus.SUCCESS)},
            graph_spec=None,
            pending_signatures=[],
            pipe_structures={
                "nomain.echo": PipeIOContract(
                    inputs={"text": PipeInputContract(concept_code="native.Text", json_schema={"type": "string"})},
                    output=PipeOutputContract(concept_code="native.Text", multiplicity=IOMultiplicity.SINGLE),
                )
            },
        )
        dispatch_mock = _enable_fake_temporal_backend(mocker, worker_result)
        open_spy = mocker.spy(get_library_manager(), "open_library")

        client = _build_client()
        response = client.post("/v1/validate", json={"mthds_contents": [NO_MAIN_PIPE_MTHDS]})

        assert response.status_code == 200, response.text
        body = response.json()
        # Same canonical envelope as the direct backend, fed by the worker's artifacts.
        assert body["bundle_blueprint"]["domain"] == "nomain"
        assert body["pipe_structures"]["nomain.echo"]["inputs"]["text"]["json_schema"] == {"type": "string"}
        assert body["validated_pipes"] == [{"pipe_ref": "nomain.echo", "status": DryRunStatus.SUCCESS}]
        assert body["pending_signatures"] == []
        assert body["is_runnable"] is True
        assert body["graph_spec"] is None
        assert body["mthds_contents"] == [NO_MAIN_PIPE_MTHDS]
        assert body["success"] is True

        # ONE dispatch carrying the request verbatim...
        dispatch_mock.assert_awaited_once()
        dispatched_arg = dispatch_mock.await_args.args[0]
        assert dispatched_arg.mthds_contents == [NO_MAIN_PIPE_MTHDS]
        assert dispatched_arg.allow_signatures is False
        # ...and ZERO API-side library loads (D10's point — the worker already had one).
        assert open_spy.call_count == 0
