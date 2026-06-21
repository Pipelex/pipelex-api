# Pipe Run

Execute pipelines with flexible input formats. Choose between synchronous execution (wait for completion) or asynchronous execution (non-blocking). Both routes are part of the [MTHDS Protocol](https://mthds.ai) — this server is the protocol's reference implementation.

> **About `mthds_contents` and MTHDS.** An "MTHDS file" is a Pipelex pipeline definition written in TOML (with the `.mthds` extension). The `mthds_contents` field on every endpoint below is a JSON array of those file contents as raw strings — typically `[open("my_pipe.mthds").read()]` from a client. You can either pass a `pipe_code` referring to a pipe already registered on the server, pass `mthds_contents` inline (the array must contain a file with a `main_pipe` property), or both.

### Execute Pipeline

Execute a Pipelex pipeline with flexible inputs and wait for completion.

**Endpoint:** `POST /v1/execute`

> **Backend selected by `execution_mode`.** `/execute` dispatches through the deployment's `execution_mode` (config default + optional policy-gated per-request `execution_mode` override), symmetric with `/start` — see [Configuration → Execution mode](configuration.md). On the orchestrator-agnostic base (`direct`, the default) it runs **in-process**; a `temporal_blocking` / `mistral_native` flavor dispatches the run to a worker and awaits it. `/execute` is **synchronous** (it returns the full output), so a fire-and-forget mode is refused with a `400` — use `POST /v1/start` for fire-and-forget. A per-request override is honored only where the deployment sets `allow_request_execution_mode_override = true`; otherwise a mode differing from the default is refused with a `403`.

**Request Body:**

```json
{
  "pipe_code": "your_pipeline_code",
  "mthds_contents": null,
  "inputs": {
    "input_name": "simple text or object"
  },
  "output_name": null,
  "output_multiplicity": null,
  "dynamic_output_concept_ref": null
}
```

**Request Fields:**

- `pipe_code` (string, optional): The code identifying the pipeline to execute. Required if no `mthds_contents` is provided, or if `mthds_contents` doesn't have a `main_pipe` property. Can be combined with `mthds_contents` to specify which pipe to execute from the provided MTHDS definition.
- `mthds_contents` (list[str], optional): Inline MTHDS file contents as raw strings (always an array, even for a single file). If provided without `pipe_code`, the bundle must contain a file with a `main_pipe` property.
- `inputs` (PipelineInputs, optional): Flexible input format - see [Input Format: PipelineInputs](#input-format-pipelineinputs) below
- `output_name` (string, optional): Name for the output slot
- `output_multiplicity` (string, optional): Output multiplicity setting (`"single"`, `"variable"`, or a specific number)
- `dynamic_output_concept_ref` (string, optional): Override output concept ref

**Validation Rules:**

- At least one of `pipe_code` or `mthds_contents` must be provided
- If only `pipe_code`: Must reference a pipe already registered in the library
- If only `mthds_contents`: Must contain a `main_pipe` property
- If both: `pipe_code` specifies which pipe to execute from the `mthds_contents`

**Response:**

```json
{
  "pipeline_run_id": "abc123...",
  "created_at": "2026-01-15T12:00:00Z",
  "state": "COMPLETED",
  "finished_at": "2026-01-15T12:00:05Z",
  "main_stuff_name": "result",
  "pipe_output": {
    "working_memory": {
      "root": { "result": { "content": { "...": "..." } } },
      "aliases": { "main_stuff": "result" }
    }
  }
}
```

**Response Fields:**

- `pipeline_run_id` (string): Unique identifier for the run.
- `created_at` (string): ISO timestamp when the pipeline was created.
- `state` (string): One of `"RUNNING"`, `"COMPLETED"`, `"FAILED"`, `"CANCELLED"`, `"ERROR"`, `"STARTED"`.
- `finished_at` (string | null): ISO timestamp when the pipeline finished, or `null` if still running.
- `main_stuff_name` (string | null): Key under `pipe_output.working_memory.root` where the main result lives. Use this to extract the typed output: `pipe_output.working_memory.root[main_stuff_name].content`.
- `pipe_output` (object): Result of the pipeline execution. Contains `working_memory` with `root` (every named stuff produced during the run) and `aliases` (built-in name mappings such as `main_stuff`).

**Errors** are returned as [RFC 7807 `application/problem+json`](error-responses.md) bodies with HTTP 4xx/5xx status codes. The successful response body has no `status`/`error` field — the HTTP status code is the source of truth.

---

### Start Pipeline

Start a pipeline execution and get its `pipeline_run_id` back with a `202` ack.

**Endpoint:** `POST /v1/start`

> **Blocking vs non-blocking depends on the deployment's execution mode.** Non-blocking fire-and-forget is a property of a **distributed** `execution_mode`: a Temporal fire-and-forget flavor enqueues the run and returns immediately with a `workflow_id`. On the orchestrator-agnostic base (`execution_mode = "direct"`, the default — see [Configuration → Execution mode](configuration.md)), the run executes **in-process** and the request blocks until completion, then answers `202` with `workflow_id: null`. The completion callback fires on the same path either way.

**Request Body:**

```json
{
  "pipe_code": "your_pipeline_code",
  "mthds_contents": null,
  "inputs": {
    "input_name": "simple text or object"
  },
  "output_name": null,
  "output_multiplicity": null,
  "dynamic_output_concept_ref": null
}
```

**Request Fields:**

- `pipe_code` (string, optional): The code identifying the pipeline to execute. Required if no `mthds_contents` is provided, or if `mthds_contents` doesn't have a `main_pipe` property.
- `mthds_contents` (list[str], optional): Inline pipeline definitions (always an array, even for a single file). If provided without `pipe_code`, must have a `main_pipe` property.
- `inputs` (PipelineInputs, optional): Flexible input format - see [Input Format: PipelineInputs](#input-format-pipelineinputs) below
- `output_name` (string, optional): Name for the output slot
- `output_multiplicity` (string, optional): Output multiplicity setting (`"single"`, `"variable"`, or a specific number)
- `dynamic_output_concept_ref` (string, optional): Override output concept ref

**Validation Rules:**

- At least one of `pipe_code` or `mthds_contents` must be provided
- If only `pipe_code`: Must reference a pipe already registered in the library
- If only `mthds_contents`: Must contain a `main_pipe` property
- If both: `pipe_code` specifies which pipe to execute from the `mthds_contents`

**Important Notes:**

- This endpoint answers `202 Accepted` immediately with a `StartAck` carrying the `pipeline_run_id`
- The pipeline continues executing in the background
- `pipe_output` will be `null` in the response (pipeline hasn't completed yet)
- The request body MAY carry a client-supplied **`pipeline_run_id`** (max 128 chars): this server honors it, and the `StartAck.pipeline_run_id` echoes it back. When absent, the server generates one. (`StartAck.pipeline_run_id` is always authoritative — protocol rule.)

**Response (202):**

```json
{
  "pipeline_run_id": "abc123...",
  "created_at": "2026-01-15T12:00:00Z",
  "state": "STARTED",
  "finished_at": null,
  "main_stuff_name": null,
  "pipe_output": null,
  "workflow_id": "pipeline-abc123..."
}
```

**Response Fields:**

- `pipeline_run_id` (string): Unique identifier for the run. Use this to correlate callbacks (see below).
- `created_at` (string): ISO timestamp when the pipeline was started.
- `state` (string): Always `"STARTED"` from this endpoint — the pipeline is queued, not finished.
- `finished_at` (null): Always `null`; the pipeline hasn't completed.
- `main_stuff_name` (null): Always `null`; populated only on the eventual completion callback.
- `pipe_output` (null): Always `null`; the result isn't ready yet.
- `workflow_id` (string | null): The orchestrator's workflow ID, when the deployment's `execution_mode` dispatches to a distributed orchestrator (e.g. a Temporal fire-and-forget flavor). `null` for the in-process `direct` mode the base ships.

**Errors** follow the same convention as `/execute`: HTTP 4xx/5xx with an [RFC 7807 `application/problem+json`](error-responses.md) body.

#### Async Completion Callbacks (optional)

`POST /v1/start` accepts an additional optional field in the request body, **`callback_urls`** — a list of HTTP(S) endpoints the server will POST to once the pipeline finishes. This is a **pipelex-api extension**, not part of the MTHDS Protocol (the protocol defines no completion channel; each implementation defines and documents its own extension args). SDK clients pass it through the generic `extra` mapping, e.g. `client.start(..., extra={"callback_urls": [...]})`.

```json
{
  "pipe_code": "your_pipeline_code",
  "inputs": { "input_name": "..." },
  "callback_urls": ["https://my-app.example.com/pipelex-finished"]
}
```

When the pipeline completes, each URL in the list receives a POST carrying:

- The completion payload in the body: `pipeline_run_id` (the protocol field), the delivery `status` (`"COMPLETED"` or `"FAILED"`), `result_url` (when results were stored), `error` (the raw `ErrorReport` dict on failure), plus the runtime's legacy `pipeline_run_id` key
- An **`X-Completion-Signature`** header — `HMAC-SHA256(secret, pipeline_run_id)` rendered as a hex digest

**Verifying the signature on the receiver side**

Your callback handler should recompute the same HMAC using its own copy of the shared secret and reject any request that doesn't match. Pseudocode:

```python
import hmac, hashlib

expected = hmac.new(
    SHARED_SECRET.encode("utf-8"),
    pipeline_run_id.encode("utf-8"),
    hashlib.sha256,
).hexdigest()

if not hmac.compare_digest(expected, request.headers["X-Completion-Signature"]):
    return Response(status=401)
```

The signer (this server) and the verifier (your callback receiver) must share the same secret value. The secret never travels over the wire — only the per-run HMAC does — so even if a callback request is intercepted, the secret stays safe.

**Server-side requirement**

Set the `COMPLETION_CALLBACK_SECRET` environment variable on the API server **only if** you use `callback_urls`. The variable is read lazily — the server boots fine without it, and only requires it when actually signing a callback. If you call `/start` with `callback_urls` and the env var isn't set, you'll get a 500 with `EnvVarNotFoundError: Environment variable 'COMPLETION_CALLBACK_SECRET' is required but not set`.

The receiver-side secret must be the same value. In typical deployments both sides pull from a shared secrets store (AWS Secrets Manager, Vault, etc.).

---

## Input Format: PipelineInputs

The `inputs` field accepts several shapes. Pipelex picks how to interpret each value from its structure — pass a string and you get `TextContent`, pass a dict with `concept` and `content` keys and you get explicit concept resolution, etc. The cases below enumerate every supported form.

### How Input Formatting Works

**Case 1: Direct Content** - Provide the value directly (simplest)

- 1.1: String → `"my text"`
- 1.2: List of strings → `["text1", "text2"]`
- 1.3: StructuredContent object → `MyClass(arg1="value")`
- 1.4: List of StuffContent objects → `[MyClass(...), MyClass(...)]`
- 1.5: ListContent of StuffContent objects → `ListContent(items=[MyClass(...), MyClass(...)])`

**Note:** Cases 1.3 and 1.5 are at the same level - both handle content types that inherit from `StuffContent`, but for different purposes (custom classes vs. list wrappers).

**Case 2: Explicit Format** - Use `{"concept": "...", "content": "..."}` for control (plain dict or DictStuff instance)

- 2.1: String with concept → `{"concept": "Text", "content": "my text"}`
- 2.2: List of strings with concept → `{"concept": "Text", "content": ["text1", "text2"]}`
- 2.3: StructuredContent object with concept → `{"concept": "Invoice", "content": InvoiceObject}`
- 2.4: List of StructuredContent objects with concept → `{"concept": "Invoice", "content": [...]}`
- 2.5: Dictionary (structured data) → `{"concept": "Invoice", "content": {"field": "value"}}`
- 2.6: List of dictionaries → `{"concept": "Invoice", "content": [{...}, {...}]}`
- 2.7: File / document URL → `{"concept": "Document", "content": {"url": "https://…/file.pdf"}}`

**Pro Tip:** For **text inputs specifically**, skip the verbose format. Just provide the string directly: `"text": "Hello"` instead of `"text": {"concept": "Text", "content": "Hello"}`

---

## Case 1: Direct Content Format

When you provide content directly (without the `concept` key), Pipelex intelligently infers the type.

### 1.1: Simple String (Text)

The simplest case - just provide a string directly:

```json
{
  "inputs": {
    "my_text": "my text"
  }
}
```

**Result:** Automatically becomes `TextContent` with concept `native.Text`

### 1.2: List of Strings (Text List)

Provide multiple text items as a list:

```json
{
  "inputs": {
    "my_texts": ["my text1", "my text2", "my text3"]
  }
}
```

**Result:** Becomes a `ListContent` containing multiple `TextContent` items

**Note:** The concept must be compatible with `native.Text` or an error will be raised.

### 1.3: StructuredContent Object

Provide a structured object directly (for Python clients):

```python
# Python client example
from my_project.domain.domain_struct import MyConcept, MySubClass

inputs = {
    "invoice_data": MyConcept(arg1="arg1", arg2=1, arg3=MySubClass(arg4="arg4"))
}
```

**What is StructuredContent?**

- `StructuredContent` is the base class for user-defined data structures in Pipelex
- You create your own classes by inheriting from `StructuredContent`
- These classes are defined in your project's Python files
- Learn more: [Python StructuredContent Classes](https://docs.pipelex.com/pages/build-reliable-ai-workflows-with-pipelex/define_your_concepts/#3-python-structuredcontent-classes)

**Concept Resolution:**

- The system searches all available domains for a concept matching the class name
- If multiple concepts with the same name exist in different domains → **Error**: Must specify domain
- If no concept is found → **Error**

### 1.4: List of StuffContent Objects

Provide multiple content objects in a plain Python list:

```python
# Python client example
inputs = {
    "invoice_list": [
        MyConcept(arg1="arg1", arg2=1, arg3=MySubClass(arg4="arg4")),
        MyConcept(arg1="arg1_2", arg2=2, arg3=MySubClass(arg4="arg4_2"))
    ]
}
```

**What it accepts:**

- Lists of `StructuredContent` objects (user-defined classes)
- Lists of native content objects (`TextContent`, `ImageContent`, etc.)

**Requirements:**

- All items must be of the same type
- Concept resolution follows the same rules as 1.3
- Creates a new `ListContent` wrapper internally

### 1.5: ListContent of StuffContent Objects

Provide an existing `ListContent` wrapper object (Python clients):

```python
# Python client example
from pipelex.core.stuffs.list_content import ListContent

inputs = {
    "invoice_list": ListContent(items=[
        MyConcept(arg1="arg1", arg2=1, arg3=MySubClass(arg4="arg4")),
        MyConcept(arg1="arg1_2", arg2=2, arg3=MySubClass(arg4="arg4_2"))
    ])
}
```

**Key Difference from Case 1.4:**

- Case 1.4: Plain Python list `[item1, item2]` → **Creates** a new `ListContent` wrapper
- Case 1.5: Already wrapped `ListContent(items=[item1, item2])` → **Uses** the wrapper directly

**Why Case 1.5 is Separate from Case 1.3:**

- `StructuredContent` and `ListContent` are **sibling classes** (both inherit from `StuffContent`)
- Case 1.3 handles user-defined structured data classes
- Case 1.5 handles list container wrappers
- They're at the same inheritance level, not parent-child

**Requirements:**

- All items within the `ListContent` must be `StuffContent` objects (this includes both `StructuredContent` and native content like `TextContent`, `ImageContent`)
- All items must be of the same type
- The `ListContent` cannot be empty
- Concept is inferred from the first item's class name (not from "ListContent")

**Use Case:** This format is useful when you already have data wrapped in a `ListContent` object from a previous pipeline execution or when working with Pipelex's internal data structures.

---

## Case 2: Explicit Format (Concept and Content)

Use the explicit format `{"concept": "...", "content": "..."}` when you need precise control over concept selection or when working with domain-specific concepts.

### 2.1: Explicit String Input

```json
{
  "inputs": {
    "text": {
      "concept": "Text",
      "content": "my text"
    }
  }
}
```

**Concept Options:**

- `"Text"` or `"native.Text"` for native text
- Any custom concept that is strictly compatible with `native.Text`

### 2.2: Explicit List of Strings

```json
{
  "inputs": {
    "documents": {
      "concept": "Text",
      "content": ["text1", "text2", "text3"]
    }
  }
}
```

**Result:** `ListContent` with multiple `TextContent` items

### 2.3: Structured Object with Concept

```json
{
  "inputs": {
    "invoice_data": {
      "concept": "Invoice",
      "content": {
        "invoice_number": "INV-001",
        "amount": 1250.00,
        "date": "2025-10-20"
      }
    }
  }
}
```

**Concept Resolution with Search Domains:**

When you specify a concept name without a domain prefix:

- ✅ If the concept exists in only one domain → Automatically found
- ❌ If the concept exists in multiple domains → **Error**: "Multiple concepts found. Please specify domain as 'domain.Concept'"
- ❌ If the concept doesn't exist → **Error**: "Concept not found"

**Using Domain Prefix:**
```json
{
  "concept": "accounting.Invoice"
}
```

This explicitly tells Pipelex to use the `Invoice` concept from the `accounting` domain.

### 2.4: List of Structured Objects

```json
{
  "inputs": {
    "invoices": {
      "concept": "Invoice",
      "content": [
        {
          "invoice_number": "INV-001",
          "amount": 1250.00
        },
        {
          "invoice_number": "INV-002",
          "amount": 890.00
        }
      ]
    }
  }
}
```

**Result:** `ListContent` with multiple structured content items

### 2.5: Dictionary Content

Provide structured data as a dictionary:

```json
{
  "inputs": {
    "person": {
      "concept": "PersonInfo",
      "content": {
        "arg1": "something",
        "arg2": 1,
        "arg3": {
          "arg4": "something else"
        }
      }
    }
  }
}
```

The system will:
1. Find the concept structure (with domain resolution as explained above)
2. Validate the dictionary against the concept's structure
3. Create the appropriate content object

### 2.6: List of Dictionaries

```json
{
  "inputs": {
    "people": {
      "concept": "PersonInfo",
      "content": [
        {
          "arg1": "something",
          "arg2": 1,
          "arg3": {"arg4": "something else"}
        },
        {
          "arg1": "something else",
          "arg2": 2,
          "arg3": {"arg4": "something else else"}
        }
      ]
    }
  }
}
```

### 2.7: File / Document Input

For pipes whose inputs declare `Document`, `Image`, or `PDF` (any URL-bearing native concept), provide a `url` inside `content`. The URL can be public HTTP(S), a `pipelex-storage://` URI (returned by `/v1/upload`), or a base64 data URL.

```json
{
  "inputs": {
    "cv": {
      "concept": "Document",
      "content": { "url": "https://example.com/resume.pdf" }
    },
    "headshot": {
      "concept": "Image",
      "content": { "url": "https://example.com/photo.jpg" }
    }
  }
}
```

**Optional fields on `Document` content:**

- `mime_type` (string) — e.g. `"application/pdf"`. Inferred from the URL when omitted.
- `filename` (string) — the original filename. Auto-extracted from local-file URLs.
- `title` (string) — display title for the document.
- `snippet` (string) — text excerpt or description.
- `public_url` (string) — public HTTPS URL when `url` is a private/storage URI.

If you pass `Document` content as a list (`[{"url": "…"}, {"url": "…"}]`), Pipelex wraps it in a `ListContent` automatically — same rules as Case 2.4.

### Using DictStuff Instances (Python Clients Only)

For Python clients, you can also pass `DictStuff` instances instead of plain dicts. `DictStuff` is a Pydantic model with the same structure as the explicit format.

```python
from pipelex.client import PipelexClient
from pipelex.core.stuffs.stuff import DictStuff

client = PipelexClient(api_token="YOUR_API_KEY")

# Using DictStuff instance with dict content
response = await client.execute_pipeline(
    pipe_code="process_invoice",
    inputs={
        "invoice": DictStuff(
            concept="accounting.Invoice",
            content={
                "invoice_number": "INV-001",
                "amount": 1250.00,
                "date": "2025-10-20"
            }
        )
    }
)

# Using DictStuff instance with list of dicts content
response = await client.execute_pipeline(
    pipe_code="process_invoices",
    inputs={
        "invoices": DictStuff(
            concept="accounting.Invoice",
            content=[
                {"invoice_number": "INV-001", "amount": 1250.00},
                {"invoice_number": "INV-002", "amount": 890.00}
            ]
        )
    }
)

# Using DictStuff instance with list of strings (for Text concept)
response = await client.execute_pipeline(
    pipe_code="analyze_texts",
    inputs={
        "documents": DictStuff(
            concept="Text",
            content=["document 1", "document 2", "document 3"]
        )
    }
)
```

**DictStuff Structure:**

- `concept` (str): The concept code (with optional domain prefix)
- `content` (dict[str, Any] | list[Any]): The actual data content

**Content Types:**

- **Dictionary**: Single structured object → Creates a single Stuff
- **List of dicts**: Multiple structured objects → Creates ListContent with validated items
- **List of strings** (for Text-compatible concepts): Creates ListContent of TextContent

**Note:** `DictStuff` instances are automatically converted to plain dicts and processed through the standard Case 2 logic.

---

## Search Domains Explained

When you reference a concept by name (like `"Invoice"` or `"PersonInfo"`), Pipelex needs to find it in your loaded domains.

### Automatic Search

```json
{
  "concept": "Invoice"
}
```

**What happens:**
1. Pipelex searches all available domains for a concept named `"Invoice"`
2. If found in **exactly one domain** → ✅ Uses that concept
3. If found in **multiple domains** → ❌ Error: "Ambiguous concept: Found 'Invoice' in domains: accounting, billing. Use 'domain.Invoice' format."
4. If **not found** → ❌ Error: "Concept 'Invoice' not found in any domain"

### Explicit Domain Specification

To avoid ambiguity, specify the domain explicitly:

```json
{
  "concept": "accounting.Invoice"
}
```

**Format:** `"domain_name.ConceptName"`

This tells Pipelex exactly which concept to use, bypassing the search.

### Best Practices

- Use simple names (`"Invoice"`) when you have unique concept names across domains
- Use domain-prefixed names (`"accounting.Invoice"`) when:
  - You have concepts with the same name in different domains
  - You want to be explicit about which concept to use
  - You're building APIs that need to be unambiguous

---

## Multiple Input Combinations

Combine different input types in a single request:

```json
{
  "inputs": {
    "text": "Analyze this contract for risks.",
    "category": {
      "concept": "Category",
      "content": {"name": "legal", "priority": "high"}
    },
    "options": ["option1", "option2", "option3"],
    "invoice": {
      "concept": "accounting.Invoice",
      "content": {
        "invoice_number": "INV-001",
        "amount": 1250.00
      }
    }
  }
}
```

In this example:

- `text` uses direct string format (Case 1.1)
- `category` uses explicit format with structured content (Case 2.5)
- `options` uses direct list format (Case 1.2)
- `invoice` uses explicit format with domain prefix and structured content (Case 2.5)

---



