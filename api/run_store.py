"""Run store for the runner-native async lifecycle (start → poll → result).

The open-source runner is stateless by default; this adds an in-process record of
in-flight and finished runs so a caller can `POST /api/v1/runs`, then poll
`GET /api/v1/runs/by-id/{id}` and `GET /api/v1/runs/by-id/{id}/result` by bare id —
the standard async request-reply pattern, without holding an HTTP connection open
and without needing Temporal, a webhook receiver, or any external datastore.

The record is LEAN and identity-free by design: the runner derives the caller from
the credential and never tracks org/user/method (those are Pipelex Platform concerns).

`RunStore` is the pluggable seam — `InMemoryRunStore` is the zero-infra default; a
durable backend (SQLite/Redis/S3) can implement the same Protocol later for
multi-replica deployments.

State machine:

    create()        PENDING
                      │  mark_running()
                      ▼
                   RUNNING
            mark_completed() │ mark_failed()
                      ▼      ▼
                COMPLETED   FAILED      (terminal)
"""

import asyncio
from typing import Protocol

from pipelex.pipeline.pipeline_response import PipelexPipelineExecuteResponse
from pipelex.types import StrEnum
from pydantic import BaseModel
from typing_extensions import override

from api.routes.pipelex.utils import get_current_iso_timestamp


class LeanRunStatus(StrEnum):
    """Lifecycle status of a runner-tracked run. A lean subset of the platform's status set."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        """True once the run is done and will not transition again."""
        match self:
            case LeanRunStatus.COMPLETED | LeanRunStatus.FAILED:
                return True
            case LeanRunStatus.PENDING | LeanRunStatus.RUNNING:
                return False


class RunRecord(BaseModel):
    """A single run's lean, identity-free state."""

    pipeline_run_id: str
    status: LeanRunStatus
    created_at: str
    finished_at: str | None = None
    result: PipelexPipelineExecuteResponse | None = None
    error: str | None = None


class RunStore(Protocol):
    """Pluggable store for runner-tracked runs. `InMemoryRunStore` is the default."""

    async def create(self, pipeline_run_id: str) -> RunRecord:
        """Register a new run in PENDING state and return its record."""
        ...

    async def mark_running(self, pipeline_run_id: str) -> None:
        """Transition a known run to RUNNING. No-op if the run is unknown."""
        ...

    async def mark_completed(self, pipeline_run_id: str, result: PipelexPipelineExecuteResponse) -> None:
        """Transition a known run to COMPLETED with its result. No-op if unknown."""
        ...

    async def mark_failed(self, pipeline_run_id: str, message: str) -> None:
        """Transition a known run to FAILED with an error message. No-op if unknown."""
        ...

    async def get(self, pipeline_run_id: str) -> RunRecord | None:
        """Return the run record, or None if the id is unknown."""
        ...


class InMemoryRunStore(RunStore):
    """Single-process, in-memory `RunStore`. Zero-infra default for self-hosted runners.

    Records live for the lifetime of the process; restarts lose history. That is the
    accepted tradeoff for the bare-container default — point a durable backend at the
    same Protocol when you need persistence or multiple replicas.
    """

    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

    @override
    async def create(self, pipeline_run_id: str) -> RunRecord:
        record = RunRecord(
            pipeline_run_id=pipeline_run_id,
            status=LeanRunStatus.PENDING,
            created_at=get_current_iso_timestamp(),
        )
        async with self._lock:
            self._records[pipeline_run_id] = record
        return record

    @override
    async def mark_running(self, pipeline_run_id: str) -> None:
        async with self._lock:
            record = self._records.get(pipeline_run_id)
            if record is not None:
                record.status = LeanRunStatus.RUNNING

    @override
    async def mark_completed(self, pipeline_run_id: str, result: PipelexPipelineExecuteResponse) -> None:
        async with self._lock:
            record = self._records.get(pipeline_run_id)
            if record is not None:
                record.status = LeanRunStatus.COMPLETED
                record.result = result
                record.finished_at = get_current_iso_timestamp()

    @override
    async def mark_failed(self, pipeline_run_id: str, message: str) -> None:
        async with self._lock:
            record = self._records.get(pipeline_run_id)
            if record is not None:
                record.status = LeanRunStatus.FAILED
                record.error = message
                record.finished_at = get_current_iso_timestamp()

    @override
    async def get(self, pipeline_run_id: str) -> RunRecord | None:
        async with self._lock:
            return self._records.get(pipeline_run_id)


def make_run_store() -> RunStore:
    """Build the configured run store. In-memory is the only backend today; the
    Protocol is the seam for a durable backend (selected by env/config) later.
    """
    return InMemoryRunStore()
