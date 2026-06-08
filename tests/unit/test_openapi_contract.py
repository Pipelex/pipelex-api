"""Contract tests for the runner's OpenAPI surface + a drift guard on docs/openapi.json.

The committed snapshot is the published, linkable contract the SDK targets. The drift
test regenerates it from the live app and asserts equality — change a route or a schema
without running `make openapi` and CI fails here.
"""

import json
from pathlib import Path

from api.main import app
from scripts.export_openapi import OPENAPI_PATH


class TestOpenApiContract:
    def test_title_is_not_fastapi_default(self):
        schema = app.openapi()
        assert schema["info"]["title"] == "Pipelex Runner API"
        assert schema["info"]["title"] != "FastAPI"
        assert schema["info"]["description"]

    def test_bearer_security_scheme_is_documented_and_optional(self):
        schema = app.openapi()
        schemes = schema["components"]["securitySchemes"]
        assert schemes["BearerAuth"]["type"] == "http"
        assert schemes["BearerAuth"]["scheme"] == "bearer"
        # Optional global requirement: `{}` allows AUTH_MODE=none, BearerAuth covers api_key/jwt.
        assert {} in schema["security"]
        assert {"BearerAuth": []} in schema["security"]

    def test_execute_and_start_request_bodies_are_documented(self):
        schema = app.openapi()
        for path in ("/api/v1/pipeline/execute", "/api/v1/pipeline/start"):
            post = schema["paths"][path]["post"]
            assert post["summary"]
            body_schema = post["requestBody"]["content"]["application/json"]["schema"]
            properties = body_schema["properties"]
            assert "pipe_code" in properties
            assert "mthds_contents" in properties
            assert "inputs" in properties
        start_props = schema["paths"]["/api/v1/pipeline/start"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "callback_urls" in start_props
        assert "pipeline_run_id" in start_props

    def test_committed_snapshot_matches_live_schema(self):
        snapshot_path = Path(OPENAPI_PATH)
        assert snapshot_path.exists(), "docs/openapi.json missing — run `make openapi`"
        committed = json.loads(snapshot_path.read_text(encoding="utf-8"))
        live = json.loads(json.dumps(app.openapi(), sort_keys=True))
        assert committed == live, "OpenAPI drift: routes/schemas changed without re-export. Run `make openapi` and commit docs/openapi.json."
