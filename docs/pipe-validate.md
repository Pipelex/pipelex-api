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

If the bundle is invalid, the endpoint returns HTTP 200 with `success: false`:

```json
{
  "success": false,
  "mthds_contents": ["..."],
  "message": "Bundle does not declare a main_pipe, which is required for validation"
}
```
