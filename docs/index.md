# Pipelex API Documentation

Welcome to the Pipelex API documentation. The API provides programmatic access to the Pipelex system.

## The three-layer contract

This server is the open-source reference implementation of the **[MTHDS Protocol](https://mthds.ai)** — the minimal HTTP contract every MTHDS runner implements. The contracts nest:

```
MTHDS Protocol  ⊂  Pipelex API (this server)  ⊂  Pipelex hosted API
(the standard)     (protocol + build tooling)    (+ durable runs, catalog, account)
```

- **MTHDS Protocol** — five routes: `POST /execute`, `POST /start`, `POST /validate`, `GET /models`, `GET /version`. Tagged `x-mthds-protocol: true` in the [committed OpenAPI artifact](openapi/pipelex-api.openapi.yaml).
- **Pipelex API (this server)** — the protocol verbatim, plus the build tooling extensions (`/build/*`) and editor tooling (`/lint`, `/format`). `/upload` and `/resolve-storage-url` exist but are NOT part of the published contract.
- **Pipelex hosted API** (`api.pipelex.com/v1`) — everything here, same shapes, plus durable runs, the method catalog, and account management.

All routes are served under the `/v1` base path (clients compose `{base}/v1/{endpoint}`).

## What the API Offers

The API currently allows you to:

1. **Run** any Pipelex pipeline with flexible inputs (sync or async)
2. **Validate** any Pipelex pipeline to ensure correctness
3. **Build** pipeline components — generate input schemas, output representations, runner code, concepts, and pipe specs
4. **Lint and format** single `.mthds` files for editor workflows
5. **List** available model presets and configurations
6. **Upload** files via presigned URLs

## Deployment

Deploy the Pipelex API anywhere that runs Docker (your laptop, ECS, Cloud Run, Kubernetes, …) using our Docker image: [`pipelex/pipelex-api`](https://hub.docker.com/r/pipelex/pipelex-api).

### 1. Run with Docker

The only required env var is `PIPELEX_GATEWAY_API_KEY`. Get a free key (with free credits) at https://app.pipelex.com — it's the default path to LLMs and gives you access to every supported model with a single credential. (If you'd rather call providers like OpenAI, Anthropic, Bedrock, or Vertex directly, you reconfigure that on the Pipelex side, not here — see https://docs.pipelex.com.)

```bash
docker run --name pipelex-api -p 8081:8081 \
  -e PIPELEX_GATEWAY_API_KEY=your-pipelex-gateway-api-key \
  pipelex/pipelex-api:latest
```

To require authentication on the API itself, add `-e AUTH_MODE=api_key -e API_KEY=your-secret` (or `AUTH_MODE=jwt` + `JWT_SECRET_KEY`). The full set of accepted env vars is documented in [Configuration](configuration.md) and `.env.example`.

If you'd rather keep config out of your shell history, use `--env-file .env` or a `docker-compose.yml` instead — see [Configuration → Setting env vars in Docker](configuration.md#setting-env-vars-in-docker) for both patterns.

To build the image yourself instead of pulling, replace `pipelex/pipelex-api:latest` with a local tag after `docker build -t pipelex-api .`.

### 2. Verify

```bash
curl http://localhost:8081/health
```

### 3. Run your first pipeline

Send an inline MTHDS bundle and inputs to `/v1/execute`:

```bash
curl -s http://localhost:8081/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "pipe_code": "summarize",
    "mthds_contents": ["domain = \"hello\"\nmain_pipe = \"summarize\"\n\n[pipe.summarize]\ntype = \"PipeLLM\"\ndescription = \"Summarize the input text in one sentence\"\ninputs = { text = \"Text\" }\noutput = \"Text\"\nprompt = \"Summarize in one sentence:\\n@text\"\n"],
    "inputs": { "text": "Pipelex turns plain-language pipeline definitions into reproducible AI workflows that run as HTTP endpoints." }
  }'
```

The response contains `state: "COMPLETED"` and the result under `pipe_output.working_memory.root.<main_stuff_name>.content`. See **[Pipe Run →](pipe-run.md)** for every input shape (text, structured objects, `Document`, `Image`, …) and the full `/execute` and `/start` reference.

### 4. Customize the configuration

Need to change the execution mode, point to a different storage backend, or ship your own model deck? See **[Configuration →](configuration.md)** for how to provide your own `.pipelex/` config files to the Docker image. The base runs every pipeline in-process by default; distributed execution (Temporal, …) is added by a deployment flavor, not configured on the base.

## Base URL

Once deployed locally, the API is available at:

```
http://localhost:8081/v1
```

## Authentication

The API supports three authentication modes via the `AUTH_MODE` environment variable:

### No Authentication (Default)

By default (`AUTH_MODE=none`), the API requires no authentication. This is the default for open-source deployments and for running behind an API Gateway that handles auth.

If you sit this API behind a trusted reverse proxy that authenticates users and forwards the caller identity via the `X-User-Id` header, set `TRUST_FORWARDED_IDENTITY_HEADERS=true` to honor it. The runner is a generic execution engine — it does not own user metadata (email, OAuth subject, auth method), so a single opaque caller id is the entire trusted surface. The value MUST be a UUID, because storage URIs are scoped under `<user_id>/...` and `/resolve-storage-url` validates the URI owner segment as a UUID. **Default is off** — without this flag, the API ignores `X-User-Id` entirely and requests stay anonymous. Only enable it when your proxy strips any inbound copy of the header before adding its own; otherwise, any external client can spoof user identity by sending it directly.

### API Key Authentication

Set `AUTH_MODE=api_key` and provide the `API_KEY` environment variable. Include it in the Authorization header:

```
Authorization: Bearer YOUR_API_KEY
```

```bash
docker run --name pipelex-api -p 8081:8081 \
  -e AUTH_MODE=api_key \
  -e API_KEY=your-api-key \
  pipelex/pipelex-api:latest
```

### JWT Authentication

Set `AUTH_MODE=jwt` and provide the `JWT_SECRET_KEY` environment variable:

```bash
docker run --name pipelex-api -p 8081:8081 \
  -e AUTH_MODE=jwt \
  -e JWT_SECRET_KEY=your-jwt-secret-key \
  pipelex/pipelex-api:latest
```

**JWT Requirements:**

- Tokens must be signed with the HS256 algorithm
- Tokens must contain a `user_id` claim whose value is a UUID. Storage URIs are scoped under `<user_id>/...` and `/resolve-storage-url` validates the URI owner segment as a UUID, so provider-issued `sub` values like `"google#abc"` are NOT accepted. Deployments using OAuth must mint their own `user_id` claim mapping each caller to a UUID.
- Pass the JWT in the Authorization header: `Authorization: Bearer YOUR_JWT_TOKEN`

## API Endpoints

### Health & Version

- `GET /health` — Health check (no auth required)
- `GET /v1/version` — MTHDS Protocol version handshake (no auth required): `{protocol_version, implementation, implementation_version, runtime_version}`. Replaces the former `/pipelex_version` and `/api_version` routes.

### Pipe Run
Execute pipelines with flexible input formats, either synchronously or asynchronously.

- `POST /v1/execute` — Run a pipeline and wait for completion (200 + full result)
- `POST /v1/start` — Start a pipeline execution without waiting (202 + `StartAck`)

[Learn more →](pipe-run.md)

### Pipe Validate
Validate MTHDS content to ensure pipelines are correctly defined before execution.

- `POST /v1/validate` — Parse, validate, and dry-run pipelines

[Learn more →](pipe-validate.md)

### MTHDS Tools
Lint and format single `.mthds` files without loading or executing a pipeline.

- `POST /v1/lint` — Return syntax, semantic, or schema diagnostics
- `POST /v1/format` — Return formatted content, changed status, and blocking syntax diagnostics

[Learn more →](mthds-tools.md)

### Pipe Builder
Generate input schemas, output representations, and runner code for pipelines.

- `POST /v1/build/inputs` — Generate example input JSON for a pipe
- `POST /v1/build/output` — Generate output representation (schema, JSON, or Python)
- `POST /v1/build/runner` — Generate Python runner code for a pipe

[Learn more →](pipe-builder.md)

### Agent
Tools for AI agents building pipelines programmatically.

- `POST /v1/build/concept` — Convert a JSON concept spec to TOML
- `POST /v1/build/pipe-spec` — Convert a JSON pipe spec to TOML
- `GET /v1/models` — The protocol model deck this runner routes to (flat `models` list, plus category-keyed `aliases`/`waterfalls` routing extensions); optional single `?type=` category filter

### Uploader (auth-gated, NON-CONTRACT)

These endpoints exist in the server but are NOT part of the published Pipelex API contract — they are deployment conveniences slated for replacement by the storage redesign. They require `AUTH_MODE=api_key` or `AUTH_MODE=jwt` — they reject anonymous requests with 401.

- `POST /v1/upload` — Upload a base64-encoded file. Returns a `pipelex-storage://…` URI you can pass back as a `Document`/`Image` `url` in subsequent pipeline calls.
- `POST /v1/resolve-storage-url` — Resolve a `pipelex-storage://…` URI to a presigned HTTPS URL (when the storage backend supports it).

For most use cases you don't need either: pass any public HTTP(S) URL (or base64 data URL) directly as `Document.content.url` and skip the upload step entirely.
