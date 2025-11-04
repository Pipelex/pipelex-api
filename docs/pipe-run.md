# Pipe Run

Execute pipelines with flexible input formats. Choose between synchronous execution (wait for completion) or asynchronous execution (non-blocking).

### Execute Pipeline

Execute a Pipelex pipeline with flexible inputs and wait for completion.

**Endpoint:** `POST /pipeline/execute`

**Request Body:**

```json
{
  "pipe_code": "your_pipeline_code",
  "plx_content": null,
  "inputs": {
    "input_name": "simple text or object"
  },
  "output_name": null,
  "output_multiplicity": null,
  "dynamic_output_concept_code": null
}
```

**Request Fields:**

- `pipe_code` (string, optional): The code identifying the pipeline to execute. Required if no `plx_content` is provided, or if `plx_content` doesn't have a `main_pipe` property. Can be combined with `plx_content` to specify which pipe to execute from the provided PLX definition.
- `plx_content` (string, optional): Inline pipeline definition. If provided without `pipe_code`, must have a `main_pipe` property.
- `inputs` (PipelineInputs, optional): Flexible input format - see [Input Format: PipelineInputs](#input-format-pipelineinputs) below
- `output_name` (string, optional): Name for the output slot
- `output_multiplicity` (string, optional): Output multiplicity setting (`"single"`, `"variable"`, or a specific number)
- `dynamic_output_concept_code` (string, optional): Override output concept code

**Validation Rules:**

- At least one of `pipe_code` or `plx_content` must be provided
- If only `pipe_code`: Must reference a pipe already registered in the library
- If only `plx_content`: Must contain a `main_pipe` property
- If both: `pipe_code` specifies which pipe to execute from the `plx_content`

**Response:**

```json
{
  "status": "success",
  "message": null,
  "error": null,
  "pipeline_run_id": "abc123...",
  "created_at": "2025-10-20T12:00:00Z",
  "pipeline_state": "COMPLETED",
  "finished_at": "2025-10-20T12:00:05Z",
  "pipe_output": {
    "working_memory": {
      "root": { ... },
      "aliases": { ... }
    },
    "pipeline_run_id": "abc123..."
  },
  "main_stuff_name": "result",
  "pipe_structures": {
    "your_pipeline_code": {
      "inputs": {
        "input_name": {
          "concept_code": "Text",
          "multiplicity": "single"
        }
      },
      "output": {
        "concept_code": "ResultType",
        "multiplicity": "single"
      }
    }
  }
}
```

**Response Fields:**

- `status` (string): Application-level status (`"success"` or `"error"`)
- `message` (string | null): Optional message providing additional information
- `error` (string | null): Optional error message when status is not `"success"`
- `pipeline_run_id` (string): Unique identifier for the pipeline run
- `created_at` (string): ISO timestamp when the pipeline was created
- `pipeline_state` (string): Current state (`"RUNNING"`, `"COMPLETED"`, `"FAILED"`, `"CANCELLED"`, `"ERROR"`, `"STARTED"`)
- `finished_at` (string | null): ISO timestamp when the pipeline finished, if completed
- `pipe_output` (object | null): Output data from the pipeline execution (contains `working_memory` and `pipeline_run_id`)
- `main_stuff_name` (string | null): Name of the main stuff in the pipeline output
- `pipe_structures` (object | null): Structure information for each pipe (inputs and output with concept codes and multiplicity)

---

### Start Pipeline

Start a pipeline execution without waiting for completion (non-blocking).

**Endpoint:** `POST /pipeline/start`

**Request Body:**

```json
{
  "pipe_code": "your_pipeline_code",
  "plx_content": null,
  "inputs": {
    "input_name": "simple text or object"
  },
  "output_name": null,
  "output_multiplicity": null,
  "dynamic_output_concept_code": null
}
```

**Request Fields:**

- `pipe_code` (string, optional): The code identifying the pipeline to execute. Required if no `plx_content` is provided, or if `plx_content` doesn't have a `main_pipe` property.
- `plx_content` (string, optional): Inline pipeline definition. If provided without `pipe_code`, must have a `main_pipe` property.
- `inputs` (PipelineInputs, optional): Flexible input format - see [Input Format: PipelineInputs](#input-format-pipelineinputs) below
- `output_name` (string, optional): Name for the output slot
- `output_multiplicity` (string, optional): Output multiplicity setting (`"single"`, `"variable"`, or a specific number)
- `dynamic_output_concept_code` (string, optional): Override output concept code

**Validation Rules:**

- At least one of `pipe_code` or `plx_content` must be provided
- If only `pipe_code`: Must reference a pipe already registered in the library
- If only `plx_content`: Must contain a `main_pipe` property
- If both: `pipe_code` specifies which pipe to execute from the `plx_content`

**Important Notes:**

- This endpoint returns immediately with a `pipeline_run_id`
- The pipeline continues executing in the background
- `pipe_output` will be `null` in the response (pipeline hasn't completed yet)

**Response:**

```json
{
  "status": "success",
  "message": null,
  "error": null,
  "pipeline_run_id": "abc123...",
  "created_at": "2025-10-20T12:00:00Z",
  "pipeline_state": "STARTED",
  "finished_at": null,
  "pipe_output": null,
  "main_stuff_name": null,
  "pipe_structures": null
}
```

**Response Fields:**

- `status` (string): Application-level status (`"success"`)
- `message` (string | null): Optional message
- `error` (string | null): Optional error message
- `pipeline_run_id` (string): Unique identifier for the pipeline run
- `created_at` (string): ISO timestamp when the pipeline was started
- `pipeline_state` (string): Current state (`"STARTED"`)
- `finished_at` (null): Always null for start endpoint (pipeline hasn't completed)
- `pipe_output` (null): Always null for start endpoint (pipeline hasn't completed)
- `main_stuff_name` (null): Always null for start endpoint
- `pipe_structures` (null): Always null for start endpoint

---

## Input Format: PipelineInputs

Run your pipeline with flexible inputs that adapt to your needs. Pipelex supports multiple formats for providing inputs, making it easy to work with simple text, structured data, or complex objects. 

### What is PipelineInputs?

The `inputs` field uses **PipelineInputs** format - a smart, flexible way to provide data to your pipelines. Instead of forcing you into a rigid structure, PipelineInputs intelligently interprets your data based on how you provide it.

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



