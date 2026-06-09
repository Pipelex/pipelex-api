"""Export the runner's OpenAPI schema to docs/openapi.json.

The committed snapshot is the human-linkable, version-controlled contract. A drift
test (tests/unit/test_openapi_contract.py) regenerates it and asserts equality, so a
route/schema change that isn't re-exported fails CI. Run `make openapi` to refresh.
"""

import json
from pathlib import Path

from api.main import fastapi_app

OPENAPI_PATH = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def export_openapi() -> Path:
    """Write the current fastapi_app.openapi() to docs/openapi.json (pretty, trailing newline)."""
    schema = fastapi_app.openapi()
    OPENAPI_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return OPENAPI_PATH


if __name__ == "__main__":
    path = export_openapi()
    print(f"Wrote {path}")
