# Configuration

This page covers how to configure the **Docker image** — the env vars it needs at boot, and how to pass your own Pipelex config files into the container.

For the syntax and meaning of Pipelex config itself (storage backends, tracing, inference routing, model decks, …), see the official Pipelex documentation: **https://docs.pipelex.com**. This page does not duplicate that.

> **The official `pipelex/pipelex-api` image is generic and orchestrator-agnostic.** It runs every pipeline **in-process** (no distributed orchestrator), with no S3, no remote tracing, and no inference credential of its own — you bring your own provider API key. Anything environment-specific is meant to be supplied by you, on top of the image, via a mounted `.pipelex/` override file. Distributed execution (Temporal, Mistral Workflows, …) is **not** built in — it is added by installing exactly one orchestrator plugin on top of this base to produce a deployment *flavor* (see "Execution mode" below).

## Environment variables

The API reads its settings from environment variables. With Docker, the easiest way is a `.env` file:

```bash
# Your inference provider key — one per provider you call, or a single
# OpenRouter key to reach many models at once. Which one you need is decided by
# the active routing profile: see "Choosing your inference provider" below.
OPENROUTER_API_KEY=your-openrouter-key
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...

# Authentication for the API itself (optional — defaults to AUTH_MODE=none)
AUTH_MODE=none                 # one of: none | api_key | jwt
API_KEY=your-api-key           # used when AUTH_MODE=api_key
JWT_SECRET_KEY=your-jwt-secret # used when AUTH_MODE=jwt

# Selects which pipelex_{PIPELEX_ENV}.toml override file is layered on top
# (see "Pipelex configuration files" below). Defaults to "dev" when unset.
PIPELEX_ENV=dev

# Required ONLY if you use POST /v1/start with `callback_urls`
# (see pipe-run.md → "Async Completion Callbacks"). HMAC secret shared between
# this server (signs callbacks) and your callback receiver (verifies them).
# Read lazily — the server boots without it; the env var is only required at
# the moment a callback signature is computed.
# COMPLETION_CALLBACK_SECRET=<shared-with-your-callback-receiver>

# Maximum request body size, in MiB, before the body-size middleware
# rejects with 413. Defaults to 100 MiB. Raise it for larger documents,
# lower it to harden the server. Read at startup — change requires a restart.
# MAX_REQUEST_BODY_MIB=100

# ── Running a method by address (`method_ref`) ────────────────────────────
# See pipe-run.md → "Running a method by address". All read at startup.
#
# The per-instance clone cache (one entry per resolved commit SHA):
# METHOD_CACHE_DIR=/var/cache/pipelex-api-methods  # default: under the OS temp dir
# MAX_METHOD_CACHE_CLONES=64          # cached clones kept before oldest-first eviction
# MAX_METHOD_CACHE_TOTAL_KIB=524288   # total bytes across cached clones (default 512 MiB)
# MAX_METHOD_CACHE_AGE_HOURS=24       # a clone unused this long is evicted
#
# Fetched-package ceilings (enforced by the pipelex runtime on every fetch):
# PIPELEX_MAX_FETCHED_PACKAGE_FILES=256       # files in the selected package
# PIPELEX_MAX_FETCHED_PACKAGE_TOTAL_KIB=8192  # bytes across the selected package (default 8 MiB)
# PIPELEX_MAX_SCANNED_MANIFESTS=100           # METHODS.toml files considered per repository
# PIPELEX_MAX_MANIFEST_FILE_KIB=256           # per METHODS.toml read during package location
```

Pipelex config TOML files can reference env vars via `${VAR}` substitution — that's how secrets like provider API keys flow from the container's environment into Pipelex's runtime config without hard-coding them. Set whichever vars your mounted `.pipelex/` files reference.

### Setting env vars in Docker

You have three idiomatic options. Pick whichever fits your workflow — they all do the same thing.

**Option 1 — `.env` file (recommended).** Edit your `.env` and pass it to the container with `--env-file`. Best for long-lived deployments and when you want the config in version control next to your `docker-compose.yml`.

```bash
# .env
OPENROUTER_API_KEY=your-openrouter-key
MAX_REQUEST_BODY_MIB=200
AUTH_MODE=api_key
API_KEY=your-strong-secret
```

```bash
docker run --name pipelex-api -p 8081:8081 --env-file .env pipelex/pipelex-api:latest
```

**Option 2 — Inline `-e` flags on `docker run`.** Best for one-off overrides or quickly testing a single value without editing files.

```bash
docker run --name pipelex-api -p 8081:8081 \
  -e OPENROUTER_API_KEY=your-openrouter-key \
  -e MAX_REQUEST_BODY_MIB=200 \
  pipelex/pipelex-api:latest
```

**Option 3 — Compose `environment:` block.** Same as `env_file:` but inline, useful when you want the values visible in `docker-compose.yml` itself.

```yaml
services:
  pipelex-api:
    image: pipelex/pipelex-api:latest
    ports: ["8081:8081"]
    env_file: .env                # for shared values + secrets
    environment:                  # for explicit per-service overrides
      MAX_REQUEST_BODY_MIB: "200"
```

You can mix `env_file:` and `environment:` — values in `environment:` win.

> **After changing any env var, restart the container.** All API-side env vars are read once at startup; live changes don't take effect until the process restarts (`docker restart pipelex-api` or `docker compose up -d` after editing).

## Pipelex configuration files

The Pipelex runtime loads `.toml` config files in a layered, deep-merged order. Later layers override earlier ones:

1. Package defaults (shipped inside the `pipelex` Python library)
2. Global config — `~/.pipelex/pipelex.toml`
3. Project config — `{cwd}/.pipelex/pipelex.toml` (if present and different from global)
4. `pipelex_local.toml`
5. `pipelex_{PIPELEX_ENV}.toml` — picked from the `PIPELEX_ENV` env var (e.g. `pipelex_dev.toml`, `pipelex_prod.toml`)
6. `pipelex_{run_mode}.toml`
7. `pipelex_override.toml` — your final override
8. `pipelex_temporary_override.toml` — ephemeral, safe for tools to write/delete

In the official Docker image, the `.pipelex/` directory shipped in this repository is copied to `/root/.pipelex` at build time and the project-level `.pipelex/` is removed from the image. That means **`/root/.pipelex/` is the single config dir the runtime reads from**, and any file you mount there participates in the layering above. To override anything, you only need to provide the keys you want to change — the layering does the rest.

For the schema and meaning of every key in these files, see https://docs.pipelex.com.

## Choosing your inference provider

The image ships no inference credential of its own: you bring your own provider API key. Two things have to agree — **the key you pass in the environment**, and **the routing profile** that decides which backend serves a model.

**One key for many models.** An [OpenRouter](https://openrouter.ai/) key reaches models from many providers through a single credential, which makes it the shortest path to a working server:

```bash
# routing_profiles_override.toml
active = "all_openrouter"
```

```bash
docker run --name pipelex-api -p 8081:8081 \
  -e OPENROUTER_API_KEY=your-openrouter-key \
  -v "$(pwd)/routing_profiles_override.toml:/root/.pipelex/inference/routing_profiles_override.toml:ro" \
  pipelex/pipelex-api:latest
```

**One key per provider.** Call a provider directly by naming its profile and passing that provider's own env var. The backend also has to be switched on (`enabled = true` in `inference/backends.toml`), since a profile naming a backend that is not enabled is refused at boot — see [Configure AI Providers](https://docs.pipelex.com/latest/get-started/configure-ai-providers/). The profiles ship in `inference/routing_profiles.toml` and the env var each backend reads is declared in `inference/backends.toml`:

| Profile | Env var it needs |
| --- | --- |
| `all_openrouter` | `OPENROUTER_API_KEY` |
| `all_openai` | `OPENAI_API_KEY` |
| `all_anthropic` | `ANTHROPIC_API_KEY` |
| `all_google` | `GOOGLE_API_KEY` |
| `all_mistral` | `MISTRAL_API_KEY` |
| `all_xai` | `XAI_API_KEY` |
| `all_groq` | `GROQ_API_KEY` |
| `all_azure_openai` | `AZURE_API_KEY` (plus the endpoint keys the backend declares) |
| `all_bedrock` | the AWS credentials your environment already provides |
| `all_vertexai` | the Google Cloud credentials your environment already provides |
| `all_ollama` | none — a local model server |

**Mixing providers.** A profile can route per model instead of sending everything to one backend: give it a `default` and a `[profiles.<name>.routes]` table keyed by model handle or pattern. `inference/routing_profiles.toml` carries worked examples, and every backend whose models a profile routes to must be enabled and have its key present. See the [inference backend reference](https://docs.pipelex.com/latest/configuration/config-technical/inference-backend-config/) for the full routing reference.

**No provider keys at all.** Point Pipelex at a local model server (Ollama, vLLM, LM Studio, llama.cpp) with the `all_ollama` profile and its base URL — or skip self-hosting and run your methods on the hosted Pipelex API at `api.pipelex.com` with a Pipelex API key from [app.pipelex.com](https://app.pipelex.com).

> `routing_profiles_override.toml` and `backends_override.toml` are deep-merged on top of the files shipped in the image, so an override only has to carry the keys it changes — you never copy the whole file. Mount them into `/root/.pipelex/inference/` exactly like any other override (see "Providing your own configuration to Docker" below).

## Orchestration mode

The base is **orchestrator-agnostic**. WHICH backend a top-level run dispatches to is a deployment choice, read from a separate **`api.toml`** config file (not the main `pipelex_{env}.toml` — the core config rejects unknown sections). It is layered exactly like the Pipelex config above, but with its own base name: `api.toml` (packaged default) → `api_{PIPELEX_ENV}.toml` → `api_override.toml`. Two keys:

| Key | Meaning | Base default |
| --- | --- | --- |
| `orchestration_mode` | Which **backend** (orchestrator) a top-level run dispatches to. An **open string token**: `direct` (in-process), `temporal`, and any plugin-provided token are accepted; an unregistered token fails loud at dispatch with the plugin's install hint. | `direct` |
| `allow_request_orchestration_mode_override` | Whether a caller may set `orchestration_mode` per request on `POST /v1/execute`, `POST /v1/start`, and `POST /v1/validate`. When `false`, a requested token that differs from the deployment default is refused with a `403`. | `false` |

The packaged default (`orchestration_mode = "direct"`, override off) is what the generic image ships.

**Boot-coherence guard.** A process boots under exactly one orchestrator (the server derives it from `orchestration_mode`), so enabling `allow_request_orchestration_mode_override` on a `direct` default is **refused at boot**: a request overriding to a non-direct mode would resolve that orchestrator's dispatch arm while the process claimed no async execution hub, failing only at dispatch. The app fails fast at startup instead — set a non-direct `orchestration_mode` default (which claims the hub, and still serves a per-request `direct` override in-process) or leave override off.

**`orchestration_mode` is one of two orthogonal axes.** It names only the **backend**. The other axis — *delivery*, i.e. whether the caller waits — is **endpoint-intrinsic**, never configured and never requestable:

- `POST /v1/execute` and `POST /v1/validate` are **synchronous** (`BLOCKING` delivery): they return the full output / the verdict.
- `POST /v1/start` is **fire-and-forget** (`FIRE_AND_FORGET` delivery): it returns immediately with a `workflow_id`. It works only on a backend that is genuinely async-capable. A Temporal deployment (`orchestration_mode = "temporal"`) enqueues on `/start` and returns a `workflow_id`. On the orchestrator-agnostic base (`orchestration_mode = "direct"`) the in-process orchestrator is blocking-only, so `/start` is **HONEST**: it refuses with a `400` (`StartRequiresAsyncOrchestration`) — use `/execute` — rather than silently running blocking and acking.

So a deployment sets **one** `orchestration_mode` and each endpoint applies its own delivery — there is no fire-and-forget token to configure or request.

**`orchestration_mode` vs `boot_orchestrator` — two knobs, two jobs.** `orchestration_mode` (here) selects the backend a **top-level entry** (`/execute`, `/start`, `/validate`) dispatches to. `boot_orchestrator` (a core Pipelex setting) selects the **execution stack** used wherever a pipe actually runs — on a distributed worker, and for the in-process scoping inside the `direct` orchestrator. On a correctly-configured deployment the two agree (a Temporal flavor sets `orchestration_mode = "temporal"` *and* boots under Temporal); keeping them distinct is what lets `orchestration_mode` be the single source of truth for top-level dispatch without coupling it to how the stack is booted. A `temporal` `orchestration_mode` still requires the process to be booted under Temporal — set them together on a Temporal flavor.

A **flavor** image (e.g. the hosted Temporal flavor) installs one orchestrator plugin and bakes an `api_{env}.toml` to flip the default, e.g.:

```toml
# api_prod.toml  (keys at the file root — no [api] wrapper)
orchestration_mode = "temporal"   # /start is fire-and-forget (async-capable); /execute + /validate stay blocking
allow_request_orchestration_mode_override = false
```

Mount your own `api_{env}.toml` / `api_override.toml` into `/root/.pipelex/` exactly like any other override file (see below).

## Providing your own configuration to Docker

Two patterns. Both rely on mounting files into `/root/.pipelex/` inside the container.

### Option 1 — Override individual files

You can mount **any single file** that lives under `/root/.pipelex/` in the image. The bind mount replaces just that one file; everything else stays as shipped. **Recommended for most users.**

Common targets:

```yaml
services:
  pipelex-api:
    image: pipelex/pipelex-api:latest
    ports: ["8081:8081"]
    env_file: .env
    volumes:
      # Layer your overrides on top of the baseline (one of the override tiers
      # — see "Pipelex configuration files" above for the load order):
      - ./pipelex_override.toml:/root/.pipelex/pipelex_override.toml:ro

      # Or env-specific (selected by PIPELEX_ENV):
      - ./pipelex_dev.toml:/root/.pipelex/pipelex_dev.toml:ro

      # Or layer the inference overrides (deep-merged — carry only what changes):
      - ./routing_profiles_override.toml:/root/.pipelex/inference/routing_profiles_override.toml:ro
      - ./backends_override.toml:/root/.pipelex/inference/backends_override.toml:ro

      # Or replace an inference layer file outright:
      - ./backends.toml:/root/.pipelex/inference/backends.toml:ro
      - ./routing_profiles.toml:/root/.pipelex/inference/routing_profiles.toml:ro

      # Or the telemetry config:
      - ./telemetry.toml:/root/.pipelex/telemetry.toml:ro
```

In other words: any `.toml` file the runtime reads from `/root/.pipelex/` (top-level files OR nested files like `inference/backends.toml`) is replaceable via a single-file bind mount. The image keeps everything else, so you only ship what differs.

For overrides that fit the layering model (`pipelex_override.toml`, `pipelex_<env>.toml`, …), Pipelex deep-merges them on top of the baseline. For files that aren't layered (like `inference/backends.toml`), the mount fully replaces the file — so include all the keys you need.

### Option 2 — Replace the entire config directory

Drop a full `.pipelex/` next to your `docker-compose.yml`:

```yaml
services:
  pipelex-api:
    image: pipelex/pipelex-api:latest
    ports: ["8081:8081"]
    env_file: .env
    volumes:
      - ./.pipelex:/root/.pipelex:ro
```

Use this when you want full control — for example, to ship your own inference backends, model deck, or routing profiles. You're now responsible for keeping the contents in sync with the version of Pipelex inside the image (the image's bundled `inference/`, `pipelex.toml`, etc. are no longer present at runtime).

### `docker run` equivalent

```bash
docker run --name pipelex-api -p 8081:8081 \
  --env-file .env \
  -v $(pwd)/pipelex_override.toml:/root/.pipelex/pipelex_override.toml:ro \
  pipelex/pipelex-api:latest
```

## Quick recipes

### Local, one OpenRouter key

`.env`:

```bash
OPENROUTER_API_KEY=your-openrouter-key
```

`routing_profiles_override.toml`:

```toml
active = "all_openrouter"
```

`docker-compose.yml`:

```yaml
services:
  pipelex-api:
    image: pipelex/pipelex-api:latest
    ports: ["8081:8081"]
    env_file: .env
    volumes:
      - ./routing_profiles_override.toml:/root/.pipelex/inference/routing_profiles_override.toml:ro
```

`docker compose up`, then `curl http://localhost:8081/health`.

### Local, with API key auth

Same as above but add:

```bash
AUTH_MODE=api_key
API_KEY=your-strong-secret
```

Clients now need `Authorization: Bearer your-strong-secret`.

### Customizing Pipelex (storage, tracing, inference, model decks, …)

Write a `pipelex_override.toml` (or env-specific `pipelex_<env>.toml`) with the keys you want to change. Reference any provider credentials from env vars via `${VAR}` so they stay out of the file. Mount it into the container as shown above. Refer to https://docs.pipelex.com for the full set of available keys and their semantics.
