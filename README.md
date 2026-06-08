<div align="center">
  <a href="https://www.pipelex.com/"><img src="https://raw.githubusercontent.com/Pipelex/pipelex/main/.github/assets/logo.png" alt="Pipelex Logo" width="400" style="max-width: 100%; height: auto;"></a>

  <h2 align="center">Pipelex API</h2>

The official REST API server for building and executing Pipelex pipelines. Deploy your pipelines as HTTP endpoints and integrate them into any application or workflow.

  <div>
    <a href="https://docs.pipelex.com/pages/api/"><strong>API Documentation</strong></a> -
    <a href="https://github.com/Pipelex/pipelex"><strong>Pipelex Core</strong></a> -
    <a href="https://go.pipelex.com/discord"><strong>Discord</strong></a>
  </div>
  <br/>

  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT License"></a>
    <a href="https://go.pipelex.com/discord"><img src="https://img.shields.io/badge/Discord-5865F2?logo=discord&logoColor=white" alt="Discord"></a>
    <a href="https://docs.pipelex.com/"><img src="https://img.shields.io/badge/Docs-03bb95?logo=read-the-docs&logoColor=white&style=flat" alt="Documentation"></a>
  </p>
</div>

---

# 📑 Table of Contents

- [Introduction](#introduction)
- [Quick Start with Docker](#-quick-start-with-docker)
- [Run your first pipeline](#-run-your-first-pipeline)
- [How to scale Pipelex](#-how-to-scale-pipelex)
- [API Documentation](#-api-documentation)
- [Support](#-support)
- [License](#-license)

# Introduction

The **Pipelex API Server** is a FastAPI-based REST API that allows you to execute [Pipelex](https://github.com/Pipelex/pipelex) pipelines via HTTP requests. Deploy your pipelines as HTTP endpoints and integrate them into any application or workflow.

# 🚀 Quick Start with Docker

**Official Docker image available at:** [`pipelex/pipelex-api`](https://hub.docker.com/r/pipelex/pipelex-api)

The published image is **generic and configuration-light**: Temporal is off, no S3, no remote tracing. It boots with a single required env var (`PIPELEX_GATEWAY_API_KEY`), and you bring your own [Pipelex configuration](docs/configuration.md) on top to enable storage, tracing, Temporal, or anything else.

### 1. Run with Docker

The only required env var is `PIPELEX_GATEWAY_API_KEY`. Get a free key (with free credits) at https://app.pipelex.com, then run:

```bash
docker run --name pipelex-api -p 8081:8081 \
  -e PIPELEX_GATEWAY_API_KEY=your-pipelex-gateway-api-key \
  pipelex/pipelex-api:latest
```

To require authentication on the API, add `-e AUTH_MODE=api_key -e API_KEY=your-secret` (or `AUTH_MODE=jwt` + `JWT_SECRET_KEY`). See [`.env.example`](.env.example) for the full list of supported variables and [docs/configuration.md](docs/configuration.md) for `--env-file` and `docker compose` patterns if you'd rather keep config out of your shell history.

If you'd rather build the image yourself instead of pulling, replace `pipelex/pipelex-api:latest` with a local tag after `docker build -t pipelex-api .`.

### 2. Verify

```bash
curl http://localhost:8081/health
```

The API is now running at `http://localhost:8081`. To customize behavior (enable Temporal, swap to S3 storage, layer in env-specific overrides, …), see [docs/configuration.md](docs/configuration.md).

# 🧪 Run your first pipeline

Once `/health` is green, `POST` an inline MTHDS bundle and inputs to `/api/v1/pipeline/execute` and you get the result back synchronously. The full walkthrough — copy-paste curl, passing `Document`/`Image` files, every input shape, and the interactive Swagger UI at `/docs` — lives in the docs so there's one source of truth:

- **[Quickstart & first pipeline →](docs/index.md)** — boot, auth modes, first `/execute` call, driving it with the `mthds` CLI.
- **[Pipe Run reference →](docs/pipe-run.md)** — every input shape and the full `/execute` + `/start` contract.
- **Interactive API reference** — `http://localhost:8081/docs` (Swagger), `/redoc`, `/openapi.json`.

# 📈 How to scale Pipelex

A single Pipelex API container is great for development, prototyping, and low-concurrency workloads — pipelines run in-process and `/api/v1/pipeline/execute` blocks the request thread until they finish.

For production-scale workloads (high concurrency, long-running pipelines, retries, durable execution, horizontal scaling), the recommended path is to run Pipelex on top of [**Temporal**](https://temporal.io/). With Temporal enabled:

- Pipeline runs become durable workflows — survive worker crashes, support retries and timeouts out of the box.
- The API container becomes a thin orchestrator: it submits workflows to a Temporal cluster and returns a `pipeline_run_id` immediately (this is what `POST /api/v1/pipeline/start` already does).
- Pipeline execution itself runs on a separate pool of **Pipelex workers** that you scale independently from the HTTP layer.
- Async completion callbacks (`callback_urls` + `X-Completion-Signature`, see [pipe-run.md](docs/pipe-run.md)) let your application be notified when each run finishes, without polling.

Pipelex already integrates with Temporal under the hood, and the Docker image accepts `TEMPORAL_API_KEY` plus a `[temporal] is_enabled = true` override in `.pipelex/`. **A complete deployment recipe (Temporal cluster sizing, worker container, autoscaling guidance, and an end-to-end docker-compose) is coming soon.** In the meantime, if you need to scale today, get in touch on [Discord](https://go.pipelex.com/discord) and we'll help you wire it up.

# 📖 API Documentation

The full reference for this API server lives next to the code in [`docs/`](docs/):

- [Overview](docs/index.md) — endpoints, authentication, deployment
- [Pipe Run](docs/pipe-run.md) — `/execute`, `/start`, every input shape
- [Pipe Validate](docs/pipe-validate.md) — `/validate`
- [Pipe Builder](docs/pipe-builder.md) — `/build/inputs`, `/build/output`, `/build/runner`
- [Configuration](docs/configuration.md) — env vars, mounting your own `.pipelex/` config

For broader Pipelex documentation (MTHDS language, concepts, pipe types, the Gateway): **[https://docs.pipelex.com/](https://docs.pipelex.com/)**

# 💬 Support

- **API Documentation**: [https://docs.pipelex.com/pages/api/](https://docs.pipelex.com/pages/api/)
- **Pipelex Documentation**: [https://docs.pipelex.com/](https://docs.pipelex.com/)
- **Discord Community**: [https://go.pipelex.com/discord](https://go.pipelex.com/discord)
- **Main Repository**: [https://github.com/Pipelex/pipelex](https://github.com/Pipelex/pipelex)

# 📝 License

This project is licensed under the [MIT license](LICENSE). Runtime dependencies are distributed under their own licenses via PyPI.

---

"Pipelex" is a trademark of Evotis S.A.S.

© 2025-2026 Evotis S.A.S.
