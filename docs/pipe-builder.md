# Pipe Builder

Generate input schemas, output representations, and Python runner code for pipelines defined in MTHDS content.

## Build Inputs

Generate example input JSON for a pipe, showing the expected input structure with concept types and placeholder content.

**Endpoint:** `POST /api/v1/build/inputs`

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

**Response:**

Returns the expected input structure as JSON, with concept types and placeholder content:

```json
{
  "cv_text": {
    "concept": "native.Text",
    "content": {
      "text": "text_value"
    }
  },
  "job_offer": {
    "concept": "native.Text",
    "content": {
      "text": "text_value"
    }
  }
}
```

---

## Build Output

Generate the output representation for a pipe in one of three formats.

**Endpoint:** `POST /api/v1/build/output`

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

**Format Options:**

- `schema` — JSON Schema representation of the output concept
- `json` — Example JSON instance of the output
- `python` — Python code defining the output class

**Response (schema format):**

```json
{
  "concept": "MatchAnalysis",
  "content": {
    "type": "object",
    "properties": {
      "score": {
        "title": "Score",
        "type": "integer",
        "description": "Match score 0-100"
      }
    },
    "required": ["score"]
  }
}
```

---

## Build Runner

Generate Python runner code for executing a pipe. Returns ready-to-use Python code with all necessary imports and setup.

**Endpoint:** `POST /api/v1/build/runner`

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

**Response:**

```json
{
  "python_code": "import asyncio\n\nfrom pipelex import pretty_print\nfrom pipelex.pipelex import Pipelex\n...",
  "pipe_code": "analyze_cv_job_match",
  "success": true,
  "message": "Runner code generated successfully"
}
```

**Response Fields:**

- `python_code` (string): Generated Python code for running the workflow
- `pipe_code` (string): Pipe code that was used
- `success` (boolean): Whether the operation was successful
- `message` (string): Status message

---

## Error Responses

All build endpoints return HTTP 500 with a detail message when an error occurs:

```json
{
  "detail": "Pipe 'nonexistent_pipe' not found in the library"
}
```

Common errors:

- Pipe code not found in the provided MTHDS content
- Invalid MTHDS content (parse errors)
- Concept validation failures
