# Pipe Builder

Generate input templates, output representations, and Python runner code for pipelines defined in MTHDS content.

All three build endpoints speak the same verdict discipline as [`POST /v1/validate`](pipe-validate.md): a **produced verdict is always a `200`** discriminated on the body's `is_valid` field. The valid arm carries the built artifact; the invalid arm carries the structured `validation_errors[]` built by pipelex's one shared error builder. Non-2xx is reserved for *no verdict could be produced* — request-shape `422`, auth `401`/`403`, server `5xx` — rendered as RFC 7807 `application/problem+json` (see [Error Responses](error-responses.md)).

## Build Inputs

Generate example input JSON for a pipe, showing the expected input structure with concept types and placeholder content.

**Endpoint:** `POST /v1/build/inputs`

**Request Body:**

```json
{
  "mthds_contents": ["domain = \"cv_matching\"\ndescription = \"CV job matching\"\nmain_pipe = \"analyze_cv_job_match\"\n\n[concept.MatchAnalysis]\ndescription = \"Match analysis result\"\n\n[concept.MatchAnalysis.structure]\nscore = { type = \"integer\", description = \"Match score 0-100\" }\n\n[pipe.analyze_cv_job_match]\ntype = \"PipeLLM\"\ndescription = \"Analyze CV against job offer\"\ninputs = { cv_text = \"native.Text\", job_offer = \"native.Text\" }\noutput = \"MatchAnalysis\""],
  "pipe_code": "analyze_cv_job_match"
}
```

**Request Fields:**

- `mthds_contents` (list[str], required): MTHDS contents to load pipes from (always an array, even for a single file)
- `pipe_code` (string, required): Pipe code to generate inputs JSON for
- `allow_signatures` (boolean, optional): Tolerate unimplemented pipe signatures in the dry-run sweep (default `false`)

**Response (valid verdict):**

```json
{
  "is_valid": true,
  "pipe_code": "analyze_cv_job_match",
  "inputs": {
    "cv_text": {
      "concept": "native.Text",
      "content": { "text": "text_value" }
    },
    "job_offer": {
      "concept": "native.Text",
      "content": { "text": "text_value" }
    }
  },
  "message": "Inputs template generated successfully"
}
```

**Response (invalid verdict):** `200` with `is_valid: false` and `validation_errors[]` — the same invalid arm every build endpoint returns:

```json
{
  "is_valid": false,
  "validation_errors": [
    { "category": "blueprint_validation", "message": "...", "source": "..." }
  ],
  "message": "..."
}
```

---

## Build Output

Generate the output representation for a pipe in one of three formats.

**Endpoint:** `POST /v1/build/output`

**Request Body:**

```json
{
  "mthds_contents": ["...your MTHDS content..."],
  "pipe_code": "analyze_cv_job_match",
  "format": "schema"
}
```

**Request Fields:**

- `mthds_contents` (list[str], required): MTHDS contents to load pipes from
- `pipe_code` (string, required): Pipe code to generate output for
- `format` (string, optional): Output format — `"schema"` (default), `"json"`, or `"python"`. **Must be lowercase.**
- `allow_signatures` (boolean, optional): Tolerate unimplemented pipe signatures in the dry-run sweep (default `false`)

**Format Options:**

- `schema` — JSON Schema representation of the output concept
- `json` — Example JSON instance of the output
- `python` — Python code defining the output class

**Response (valid verdict, schema format):**

```json
{
  "is_valid": true,
  "pipe_code": "analyze_cv_job_match",
  "format": "schema",
  "output": {
    "concept": "MatchAnalysis",
    "content": {
      "type": "object",
      "properties": {
        "score": { "title": "Score", "type": "integer", "description": "Match score 0-100" }
      },
      "required": ["score"]
    }
  },
  "message": "Output representation generated successfully"
}
```

The invalid verdict is the shared `is_valid: false` arm shown under Build Inputs.

---

## Build Runner

Generate a Python runner script for executing a pipe. The script's imports, example inputs, and output cast are spelled with the **emitted** class names of the typed-structures projection, and the response carries that projection alongside the script — the same stamped `structures.py` + `codegen.lock` a local `pipelex build runner` scaffolds (see [Resolve & Codegen](codegen.md) for the stamp/lock trust chain).

**Endpoint:** `POST /v1/build/runner`

**Request Body:**

```json
{
  "mthds_contents": ["...your MTHDS content..."],
  "pipe_code": "analyze_cv_job_match"
}
```

**Request Fields:**

- `mthds_contents` (list[str], required): MTHDS contents to load and generate runner code for
- `pipe_code` (string, required): Pipe code to generate runner code for
- `allow_signatures` (boolean, optional): Tolerate unimplemented pipe signatures in the dry-run sweep (default `false`)

**Response (valid verdict):**

```json
{
  "is_valid": true,
  "pipe_code": "analyze_cv_job_match",
  "python_code": "import sys\nfrom pathlib import Path\n...",
  "structures": {
    "directory": "structures",
    "artifacts": [
      { "path": "structures.py", "content": "# >>> pipelex-codegen-stamp >>>\n..." }
    ],
    "lock": "# codegen.lock — generated artifact set (Pipelex codegen). Do not edit by hand.\n...",
    "lock_filename": "codegen.lock"
  },
  "message": "Runner code generated successfully"
}
```

To materialize a runnable tree, write `python_code` as the runner script and each `structures.artifacts[]` entry (plus `structures.lock` as `structures.lock_filename`) into the `structures.directory` beside it — the script imports from there (`from structures.structures import ...`).

The invalid verdict — including a failed dry-run of the requested pipe — is the shared `is_valid: false` arm shown under Build Inputs. One no-verdict special case: a requested pipe whose cross-package dependencies are absent from the request (recorded SKIPPED by the sweep) is a request-shape `422`, since no runner can be honestly generated without its dependency closure.
