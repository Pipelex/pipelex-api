# Pipe Validate

Validate MTHDS content by parsing, loading, and dry-running pipes without executing them.

**Endpoint:** `POST /api/v1/validate`

**Request Body:**

```json
{
  "mthds_contents": ["domain = \"my_domain\"\ndescription = \"My domain\"\nmain_pipe = \"my_pipe\"\n\n[concept.MyResult]\ndescription = \"A result\"\n\n[pipe.my_pipe]\ntype = \"PipeLLM\"\ndescription = \"Process input\"\ninputs = { text = \"native.Text\" }\noutput = \"MyResult\""]
}
```

**Request Fields:**

- `mthds_contents` (list[str], required): MTHDS contents to validate (always an array, even for a single file)

**Response:**

```json
{
  "mthds_contents": ["..."],
  "pipelex_bundle_blueprint": {
    "domain": "my_domain",
    "description": "My domain",
    "main_pipe": "my_pipe",
    "concepts": { ... },
    "pipes": { ... }
  },
  "graph_spec": { ... },
  "pipe_structures": {
    "my_pipe": {
      "inputs": {
        "text": {
          "concept_code": "native.Text",
          "json_schema": { ... }
        }
      },
      "output": {
        "concept_code": "MyResult",
        "multiplicity": "single"
      }
    }
  },
  "success": true,
  "message": "MTHDS content validated successfully"
}
```

**Response Fields:**

- `mthds_contents` (list[str]): The MTHDS contents that were validated
- `pipelex_bundle_blueprint` (object): The parsed bundle blueprint with domain, concepts, and pipes
- `graph_spec` (object | null): Execution graph specification from the dry run
- `pipe_structures` (object): Per-pipe input/output JSON Schema structures
- `success` (boolean): Whether the validation was successful
- `message` (string): Status message

**What This Endpoint Does:**

1. Parses MTHDS content into bundle blueprints
2. Finds the primary blueprint (the one with `main_pipe`)
3. Loads pipes into the library
4. Runs a dry-run pipeline to validate execution flow
5. Builds per-pipe input/output structures with JSON Schema
6. Returns validation results with blueprint, graph spec, and pipe structures

**Execution Backends:**

The endpoint behaves identically on both deployment backends; only where the work runs differs:

- **Direct (Temporal disabled):** the validation sweep and the graph dry-run both run in-process in the API server.
- **Temporal enabled:** the API dispatches the whole job — validation sweep **and** graph dry-run — to a worker as **one** in-process activity (`wf_dry_validate` → `act_dry_validate`) and awaits the result in a single round-trip. The activity traces the graph in an in-memory event log (no tracing-backend I/O) and returns the `graph_spec` on the activity result. Validation failures cross the boundary as structured error reports carrying the same `error_type=ValidateBundleError` / `error_domain=input` identity, so the 422 problem document is byte-for-byte the same contract as the direct path.

The graph remains best-effort on both backends: a bundle that validates but whose graph dry-run fails still returns 200 with `graph_spec: null`.

**Error Responses:**

If the bundle is invalid, the endpoint returns **HTTP 422** with an [RFC 7807 problem document](error-responses.md):

```json
{
  "type": "https://docs.pipelex.com/latest/errors/validation-error/",
  "title": "Validation error",
  "status": 422,
  "detail": "Bundle does not declare a main_pipe, which is required for validation",
  "instance": "/api/v1/validate",
  "error_type": "ValidationError",
  "error_domain": "input",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

The HTTP status code is the source of truth — a 200 always means success, and the success body never carries a `success: false` failure mode. See [Error Responses](error-responses.md) for the full envelope and disclosure modes.
