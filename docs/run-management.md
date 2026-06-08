# Run Management & Polling

The Pipelex API documented elsewhere in this site is **the runner** — open source, self-hostable, and **execution-only**. It runs a pipeline and gives you the result back: synchronously via [`/pipeline/execute`](pipe-run.md), or fire-and-forget via [`/pipeline/start`](pipe-run.md) with a completion callback. It deliberately keeps **no run store**: no `pipeline_run_id` to poll, no run state, no result retention.

For many use cases — long-running pipelines, dashboards, retries, fan-out — you want the opposite: start a run, get an id back immediately, and **poll that id** for status and result without holding a connection open. That durable run lifecycle is a separate layer that sits **on top of** the runner. This page documents the shape of that layer.

!!! note "This is a proposal, not the standard"
    This run-management API is **Pipelex-specific**. It is **not** part of the [MTHDS open standard](https://mthds.ai) and the standard does not require it — MTHDS defines methods, not how you operate runs over HTTP. What follows is simply the API **Pipelex proposes** for managing runs. Pipelex operates one implementation of it (closed source) as the hosted platform behind `app.pipelex.com`. You are free to implement your own, ignore it, or design a different surface entirely.

## Why it's a separate layer

The runner is a generic execution engine. It identifies a caller by the credential, never by a request argument, and it owns no user/org/method metadata — so durable, multi-tenant run tracking simply doesn't belong in it. Keeping the runner stateless is what lets it stay a small, open, self-hostable container with no database, queue, or object store required.

Run management is the stateful counterpart: it allocates run ids, persists run state and results, enforces identity/ownership, and exposes a polling surface. It is the natural home for everything the runner refuses to do.

```
Client / SDK
    │  start run, poll by id
    ▼
Run-management layer  ── persists run records, owns identity & durability
    │  executes via the runner's HTTP contract
    ▼
Pipelex runner (this API)  ── stateless: /pipeline/execute or /pipeline/start
```

## The contract Pipelex proposes

A run-management layer exposes three operations. The request body for starting a run is **identical to the runner's** `/pipeline/execute` body (`pipe_code`, `mthds_contents`, `inputs`, `output_name`, …) — the layer is a manager around the same execution, not a different way to describe a pipeline.

### Start a run

```
POST /runs
```

Body: the same pipeline body as `/pipeline/execute`. Returns immediately with an id to poll:

```json
{
  "pipeline_run_id": "1f3c…",
  "status": "PENDING",
  "created_at": "2026-06-08T12:00:00Z"
}
```

### Get run status

```
GET /runs/by-id/{pipeline_run_id}
```

```json
{
  "pipeline_run_id": "1f3c…",
  "status": "RUNNING",
  "created_at": "2026-06-08T12:00:00Z",
  "finished_at": null
}
```

### Get run result

```
GET /runs/by-id/{pipeline_run_id}/result
```

A single-shot result lookup whose **HTTP status encodes the run state**:

| Run state | HTTP | Body |
| --- | --- | --- |
| Pending / running | `202` + `Retry-After` | empty — poll again after the suggested delay |
| Completed | `200` | the runner's `/pipeline/execute` response, verbatim |
| Failed (terminal) | `409` | a structured error detail (`error_type`, `message`, `status`) |
| Unknown id | `404` | a structured `not found` detail |

The status field follows a simple, terminal-once state machine:

```
PENDING ──▶ RUNNING ──▶ COMPLETED
                   └───▶ FAILED
```

`COMPLETED` and `FAILED` are terminal; `created_at` is set on start, `finished_at` on reaching a terminal state.

## What an implementation owns (and the runner doesn't)

The contract above is intentionally silent on **how** you fulfil it — that is the whole point of separating the layers. An implementation chooses:

- **Identity & ownership** — who may start a run and who may read its id. The runner is identity-light by design; multi-tenant isolation (per-user / per-org scoping of run ids) lives here.
- **Durability** — where run records and results are stored, and for how long. An in-memory map, SQLite, Redis, Postgres, DynamoDB + object storage — the runner doesn't care.
- **Execution strategy** — how `POST /runs` reaches the runner. The reference behavior: persist a `PENDING` record, then drive the runner — either `/pipeline/start` with a completion `callback_url` that flips the record to `COMPLETED`/`FAILED`, or `/pipeline/execute` inside your own worker/queue — and serve polling from the record.
- **Operational concerns** — retries, self-healing of stuck runs, rate limits, quotas, billing, audit. None of these belong in the runner.

Because the start body and the completed result body are exactly the runner's, an SDK can drive both tiers with one code path: point it at a run-management base URL when you have one, and at the runner's `/pipeline/execute` when you don't.

## Pipelex's implementation

The hosted Pipelex Platform is one closed-source implementation of this contract, with durable storage, WorkOS-based identity, multi-tenant org scoping, retries, and billing layered on. None of that is required to be compatible — it's simply how Pipelex runs it. If you self-host the runner and want polling, implement this small surface against your own store; the open-source runner underneath stays unchanged.
