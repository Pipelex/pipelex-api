# Pipe Builder

Generate a pipeline from a brief description using AI, then optionally generate Python runner code for it.

## Build Pipeline

**Endpoint:** `POST /pipe-builder/build`

**Request Body:**

```json
{
  "brief": "Extract invoice information from a PDF document"
}
```

**Request Fields:**

- `brief` (string, required): Brief description of the pipeline to build

**Response:**

```json
{
  "plx_content": "domain = \"invoice_extraction\"\n\n[concept]\nInvoice = \"An invoice document\"\n...",
  "pipelex_bundle_blueprint": {
    "domain": "invoice_extraction",
    "concepts": { ... },
    "pipes": { ... }
  },
  "pipe_structures": {
    "extract_invoice": {
      "inputs": { ... },
      "output": { ... }
    }
  },
  "success": true,
  "message": "Pipeline generated successfully"
}
```

**Example:**

Request:
```json
{
  "brief": "Extract invoice information from a PDF document"
}
```

Response:
```json
{
  "plx_content": "domain = \"invoice_extraction\"\ndescription = \"Extract invoice information from PDF documents\"\n\n[concept]\nInvoice = \"An invoice document with structured information\"\n\n[concept.Invoice.structure]\ninvoice_number = { type = \"text\", description = \"The invoice number\", required = true }\ndate = { type = \"date\", description = \"Invoice date\", required = true }\ntotal_amount = { type = \"number\", description = \"Total amount\", required = true }\nvendor_name = { type = \"text\", description = \"Vendor name\", required = true }\n\n[pipe.extract_invoice]\ntype = \"PipeSequence\"\ndescription = \"Extract invoice information from PDF\"\ninputs = { document = \"PDF\" }\noutput = \"Invoice\"\nsteps = [\n  { pipe = \"extract_text\", result = \"text\" },\n  { pipe = \"parse_invoice\", result = \"invoice\" }\n]\n\n[pipe.extract_text]\ntype = \"PipeExtract\"\ndescription = \"Extract text from PDF\"\ninputs = { document = \"PDF\" }\noutput = \"Page\"\n\n[pipe.parse_invoice]\ntype = \"PipeLLM\"\ndescription = \"Parse invoice information\"\ninputs = { pages = \"Page[]\" }\noutput = \"Invoice\"\nmodel = { model = \"gpt-4o\", temperature = 0.1 }\nprompt = \"\"\"\nExtract the invoice information from the following pages:\n\n@pages\n\"\"\"",
  "pipelex_bundle_blueprint": {
    "domain": "invoice_extraction",
    "description": "Extract invoice information from PDF documents",
    "concepts": {
      "Invoice": {
        "concept_code": "Invoice",
        "description": "An invoice document with structured information",
        "structure": {
          "invoice_number": {
            "type": "text",
            "description": "The invoice number",
            "required": true
          },
          "date": {
            "type": "date",
            "description": "Invoice date",
            "required": true
          },
          "total_amount": {
            "type": "number",
            "description": "Total amount",
            "required": true
          },
          "vendor_name": {
            "type": "text",
            "description": "Vendor name",
            "required": true
          }
        }
      }
    },
    "pipes": {
      "extract_invoice": { "...": "..." },
      "extract_text": { "...": "..." },
      "parse_invoice": { "...": "..." }
    }
  },
  "pipe_structures": {
    "extract_invoice": {
      "inputs": {
        "document": {
          "concept_code": "PDF",
          "multiplicity": "single"
        }
      },
      "output": {
        "concept_code": "Invoice",
        "multiplicity": "single"
      }
    }
  },
  "success": true,
  "message": "Pipeline generated successfully"
}
```

---

## Generate Runner Code

Generate Python runner code for executing a pipeline.

**Endpoint:** `POST /pipe-builder/generate-runner`

**Request Body:**

```json
{
  "plx_content": "domain = \"my_domain\"\n\n[concept]\nMyResult = \"A result\"\n\n[pipe.my_pipe]\ntype = \"PipeLLM\"\ndescription = \"Process input\"\noutput = \"MyResult\"\nprompt = \"Generate output\"",
  "pipe_code": "my_pipe"
}
```

**Request Fields:**

- `plx_content` (string, required): PLX content to load and generate runner code for
- `pipe_code` (string, required): Pipe code to generate runner code for

**Response:**

```json
{
  "python_code": "import asyncio\n\nfrom pipelex import pretty_print\nfrom pipelex.pipelex import Pipelex\nfrom pipelex.pipeline.execute import execute_pipeline\n\n\nasync def my_pipe() -> str:\n    ...",
  "pipe_code": "my_pipe",
  "success": true,
  "message": "Runner code generated successfully"
}
```

