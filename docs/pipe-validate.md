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

**Error Responses:**

If the bundle is invalid, the endpoint returns **HTTP 422** with an [RFC 7807 problem document](error-responses.md):

```json
{
  "type": "https://docs.pipelex.com/latest/errors/validate-bundle-error/",
  "title": "Validate bundle error",
  "status": 422,
  "detail": "Bundle does not declare a main_pipe, which is required for validation.",
  "instance": "/api/v1/validate",
  "error_type": "ValidateBundleError",
  "error_domain": "input",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

The HTTP status code is the source of truth — a 200 always means success, and the success body never carries a `success: false` failure mode. See [Error Responses](error-responses.md) for the full envelope and disclosure modes.
