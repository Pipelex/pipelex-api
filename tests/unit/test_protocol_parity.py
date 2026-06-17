"""Local ↔ hosted parity of the MTHDS Protocol surfaces (the Phase 3a E2E diff).

The alignment's end-to-end claim: a client of the Pipelex family can write portable code
across the local runtime and the hosted API. Each scenario here calls the local
`PipelexMTHDSProtocol` directly AND the HTTP route with the same payload, then asserts the
shared report keys are byte-identical and the wire extras (`mthds_contents`, `message`)
appear on the HTTP envelope only. `is_valid` is a canonical report field on both backends (not a
wire extra — the `success` extra is retired). `graph_spec` is compared by presence/absence,
not value: it carries run-specific identity (graph id, node timings, random dry-run data),
so two runs never serialize identically.

The validate fixtures mirror the protocol-alignment baseline scenarios: the lenient
signature batch, the strict header+definition batch, the no-`main_pipe` batch (the D2
regression surface), and a complete main-pipe bundle (the only one producing a graph).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mthds.protocol.models import ModelCategory
from pipelex.pipeline.runner import PipelexMTHDSProtocol

from api.exception_handlers import register_exception_handlers
from api.routes import router as api_router
from tests.unit._constants import HEADER_AND_DEFINITION_BATCH, NO_MAIN_PIPE_MTHDS, SIGNATURE_ONLY_BATCH, VALID_MTHDS

# The hosted /validate envelope = canonical report + exactly these wire-only extras.
VALIDATE_WIRE_EXTRAS = {"mthds_contents", "message"}


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/v1")
    register_exception_handlers(app)
    return TestClient(app)


@pytest.mark.asyncio(loop_scope="class")
class TestProtocolParity:
    @pytest.mark.parametrize(
        ("mthds_contents", "allow_signatures"),
        [
            pytest.param(SIGNATURE_ONLY_BATCH, True, id="signature_only-lenient"),
            pytest.param(HEADER_AND_DEFINITION_BATCH, False, id="header_and_definition-strict"),
            pytest.param([NO_MAIN_PIPE_MTHDS], False, id="no_main_pipe-strict"),
            pytest.param([VALID_MTHDS], False, id="complete_main_pipe-strict"),
        ],
    )
    async def test_validate_parity(self, mthds_contents: list[str], allow_signatures: bool):
        local_report = await PipelexMTHDSProtocol().validate(mthds_contents=mthds_contents, allow_signatures=allow_signatures)
        # Same serialization options as the route's JSONResponse path.
        local_dump = local_report.model_dump(mode="json", serialize_as_any=True, by_alias=True)

        client = _build_client()
        response = client.post("/v1/validate", json={"mthds_contents": mthds_contents, "allow_signatures": allow_signatures})
        assert response.status_code == 200, response.text
        body = response.json()

        # The wire extras are HTTP-only — and they are the ONLY divergence in key sets.
        assert VALIDATE_WIRE_EXTRAS.isdisjoint(local_dump)
        assert set(body) == set(local_dump) | VALIDATE_WIRE_EXTRAS

        # graph_spec: presence parity (run-specific content — see module docstring).
        assert (body["graph_spec"] is None) == (local_dump["graph_spec"] is None)

        # Every other shared key is identical between the two backends.
        for key in sorted(set(local_dump) - {"graph_spec"}):
            assert body[key] == local_dump[key], f"local/hosted divergence on shared key {key!r}"

        # The extras carry what the webapp depends on; `is_valid` is the canonical valid-arm discriminant.
        assert body["mthds_contents"] == mthds_contents
        assert body["is_valid"] is True
        assert body["message"]

    async def test_models_parity_unfiltered(self):
        local_deck = await PipelexMTHDSProtocol().models()
        local_dump = local_deck.model_dump(mode="json", serialize_as_any=True, by_alias=True)

        client = _build_client()
        response = client.get("/v1/models")
        assert response.status_code == 200, response.text
        body = response.json()

        # /models has no wire extras: the HTTP body IS the local deck, byte-identical.
        assert body == local_dump
        # Guard against vacuous parity (two empty decks would also be "identical").
        assert body["models"]
        assert body["aliases"]
        assert body["waterfalls"]

    async def test_models_parity_filtered(self):
        local_deck = await PipelexMTHDSProtocol().models(category=ModelCategory.LLM)
        local_dump = local_deck.model_dump(mode="json", serialize_as_any=True, by_alias=True)

        client = _build_client()
        response = client.get("/v1/models", params={"type": "llm"})
        assert response.status_code == 200, response.text
        body = response.json()

        assert body == local_dump
        assert body["models"]
        assert all(entry["type"] == "llm" for entry in body["models"])
