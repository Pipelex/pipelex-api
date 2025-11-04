# Pipe Validate

Validate PLX content by parsing, loading, and dry-running pipes without executing them.

**Endpoint:** `POST /validate`

**Request Body:**

```json
{
  "plx_content": "domain = \"my_domain\"\n\n[concept]\nMyResult = \"A result\"\n\n[pipe.my_pipe]\ntype = \"PipeLLM\"\ndescription = \"Process input\"\noutput = \"MyResult\"\nprompt = \"Generate output\""
}
```

**Request Fields:**

- `plx_content` (string, required): PLX content to validate

**Response:**

```json
{
  "plx_content": "domain = \"my_domain\"...",
  "pipelex_bundle_blueprint": {
    "domain": "my_domain",
    "concepts": { ... },
    "pipes": { ... }
  },
  "pipe_structures": {
    "my_pipe": {
      "inputs": { ... },
      "output": { ... }
    }
  },
  "success": true,
  "message": "PLX content validated successfully"
}
```

**What This Endpoint Does:**

1. Parses PLX content into a bundle blueprint
2. Loads pipes into the library
3. Runs static validation and dry runs
4. Returns validation results with blueprint and pipe structures
5. Cleans up loaded pipes after validation

**Example:**

Request:
```json
{
  "plx_content": "domain = \"greeting\"\n\n[concept]\nGreeting = \"A friendly greeting\"\n\n[pipe.hello]\ntype = \"PipeLLM\"\ndescription = \"Generate a greeting\"\noutput = \"Greeting\"\nmodel = { model = \"gpt-4o-mini\", temperature = 0.7 }\nprompt = \"Generate a friendly greeting\""
}
```

Response:
```json
{
  "plx_content": "domain = \"greeting\"\n\n[concept]\nGreeting = \"A friendly greeting\"\n\n[pipe.hello]\ntype = \"PipeLLM\"\ndescription = \"Generate a greeting\"\noutput = \"Greeting\"\nmodel = { model = \"gpt-4o-mini\", temperature = 0.7 }\nprompt = \"Generate a friendly greeting\"",
  "pipelex_bundle_blueprint": {
    "domain": "greeting",
    "description": null,
    "concepts": {
      "Greeting": {
        "concept_code": "Greeting",
        "description": "A friendly greeting",
        "refines": null,
        "structure": null
      }
    },
    "pipes": {
      "hello": {
        "pipe_code": "hello",
        "type": "PipeLLM",
        "description": "Generate a greeting",
        "inputs": {},
        "output": {
          "concept_code": "Greeting",
          "multiplicity": "single"
        }
      }
    }
  },
  "pipe_structures": {
    "hello": {
      "inputs": {},
      "output": {
        "concept_code": "Greeting",
        "multiplicity": "single"
      }
    }
  },
  "success": true,
  "message": "PLX content validated successfully"
}
```
