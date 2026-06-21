# Configuration

This page covers how to configure the **Docker image** — the env vars it needs at boot, and how to pass your own Pipelex config files into the container.

For the syntax and meaning of Pipelex config itself (storage backends, tracing, inference routing, model decks, …), see the official Pipelex documentation: **https://docs.pipelex.com**. This page does not duplicate that.

> **The official `pipelex/pipelex-api` image is generic and orchestrator-agnostic.** It runs every pipeline **in-process** (no distributed orchestrator), with no S3, no remote tracing, and the Pipelex Gateway as the only enabled inference backend. Anything environment-specific is meant to be supplied by you, on top of the image, via a mounted `.pipelex/` override file. Distributed execution (Temporal, Mistral Workflows, …) is **not** built in — it is added by installing exactly one orchestrator plugin on top of this base to produce a deployment *flavor* (see "Execution mode" below).

## Environment variables

The API reads its settings from environment variables. With Docker, the easiest way is a `.env` file:

```bash
# Pipelex Gateway API key — used to call LLMs through Pipelex's inference layer.
# Required only if you keep the default routing profile. If you reconfigure
# Pipelex to call providers directly (OpenAI, Anthropic, Bedrock, …), you'll
# need those providers' own env vars instead — see https://docs.pipelex.com.
PIPELEX_GATEWAY_API_KEY=your-pipelex-gateway-key

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

# Maximum decoded size, in MiB, accepted by POST /v1/upload. Defaults to
# 50 MiB. Raise it for larger documents, lower it to harden the server.
# Read at startup — change requires a restart.
# MAX_UPLOAD_MIB=50
```

Pipelex config TOML files can reference env vars via `${VAR}` substitution — that's how secrets like provider API keys flow from the container's environment into Pipelex's runtime config without hard-coding them. Set whichever vars your mounted `.pipelex/` files reference.

### Setting env vars in Docker

You have three idiomatic options. Pick whichever fits your workflow — they all do the same thing.

**Option 1 — `.env` file (recommended).** Edit your `.env` and pass it to the container with `--env-file`. Best for long-lived deployments and when you want the config in version control next to your `docker-compose.yml`.

```bash
# .env
PIPELEX_GATEWAY_API_KEY=your-pipelex-gateway-key
MAX_UPLOAD_MIB=200
AUTH_MODE=api_key
API_KEY=your-strong-secret
```

```bash
docker run --name pipelex-api -p 8081:8081 --env-file .env pipelex/pipelex-api:latest
```

**Option 2 — Inline `-e` flags on `docker run`.** Best for one-off overrides or quickly testing a single value without editing files.

```bash
docker run --name pipelex-api -p 8081:8081 \
  -e PIPELEX_GATEWAY_API_KEY=your-pipelex-gateway-key \
  -e MAX_UPLOAD_MIB=200 \
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
      MAX_UPLOAD_MIB: "200"
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

## Execution mode

The base is **orchestrator-agnostic**. WHICH backend a top-level run dispatches as is a deployment choice, read from a separate **`api.toml`** config file (not the main `pipelex_{env}.toml` — the core config rejects unknown sections). It is layered exactly like the Pipelex config above, but with its own base name: `api.toml` (packaged default) → `api_{PIPELEX_ENV}.toml` → `api_override.toml`. Two keys:

| Key | Meaning | Base default |
| --- | --- | --- |
| `execution_mode` | Which **backend** a top-level run dispatches as, named in its **synchronous** form — `direct` (in-process), `temporal_blocking`, or `mistral_native`. A mode whose orchestrator plugin is **not installed** fails loud at dispatch with the plugin's install hint. | `direct` |
| `allow_request_execution_mode_override` | Whether a caller may set `execution_mode` per request on `POST /v1/execute`, `POST /v1/start`, and `POST /v1/validate`. When `false`, a requested mode that differs from the deployment default is refused with a `403`. | `false` |

The packaged default (`execution_mode = "direct"`, override off) is what the generic image ships.

**Fire-and-forget is a property of the endpoint, not of the deployment.** `execution_mode` names the synchronous backend; each endpoint then dispatches the right variant of it:

- `POST /v1/execute` and `POST /v1/validate` are **synchronous** (they return the full output / the verdict) and dispatch `execution_mode` as-is.
- `POST /v1/start` is **asynchronous** and dispatches the **fire-and-forget sibling** of the configured backend when one exists. So a Temporal deployment (`execution_mode = "temporal_blocking"`) enqueues on `/start` and returns a `workflow_id` immediately, while `direct` / `mistral_native` have no fire-and-forget variant and dispatch unchanged, blocking until completion — `direct` answers `202` with `workflow_id: null`, and `mistral_native` answers with the `workflow_id` its orchestrator returns (the run id).

So a deployment sets **one** coherent `execution_mode` and every endpoint does the right thing — you never configure `temporal_fire_and_forget` directly. (Explicitly requesting `temporal_fire_and_forget` per request on `/execute` is still refused with a `400` — `/execute` is synchronous.)

**`execution_mode` vs `boot_orchestrator` — two knobs, two jobs.** `execution_mode` (here) selects the backend a **top-level entry** (`/execute`, `/start`, `/validate`) dispatches to. `boot_orchestrator` (a core Pipelex setting) selects the **execution stack** used wherever a pipe actually runs — on a distributed worker, and for the in-process scoping inside the `direct` orchestrator. On a correctly-configured deployment the two agree (a Temporal flavor sets `execution_mode = "temporal_blocking"` *and* boots under Temporal); keeping them distinct is what lets `execution_mode` be the single source of truth for top-level dispatch without coupling it to how the stack is booted. A `temporal_*` `execution_mode` still requires the process to be booted under Temporal — set them together on a Temporal flavor.

A **flavor** image (e.g. the hosted Temporal flavor) installs one orchestrator plugin and bakes an `api_{env}.toml` to flip the default, e.g.:

```toml
# api_prod.toml  (keys at the file root — no [api] wrapper)
execution_mode = "temporal_blocking"   # /start derives temporal_fire_and_forget; /execute + /validate stay blocking
allow_request_execution_mode_override = false
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

      # Or replace inference layer files directly:
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

### Local, default everything

`.env`:

```bash
PIPELEX_GATEWAY_API_KEY=your-pipelex-gateway-key
```

`docker-compose.yml`:

```yaml
services:
  pipelex-api:
    image: pipelex/pipelex-api:latest
    ports: ["8081:8081"]
    env_file: .env
```

`docker compose up`, then `curl http://localhost:8081/health`. No override file needed.

### Local, with API key auth

Same as above but add:

```bash
AUTH_MODE=api_key
API_KEY=your-strong-secret
```

Clients now need `Authorization: Bearer your-strong-secret`.

### Customizing Pipelex (storage, tracing, inference, model decks, …)

Write a `pipelex_override.toml` (or env-specific `pipelex_<env>.toml`) with the keys you want to change. Reference any provider credentials from env vars via `${VAR}` so they stay out of the file. Mount it into the container as shown above. Refer to https://docs.pipelex.com for the full set of available keys and their semantics.
