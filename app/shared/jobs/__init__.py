"""Job queue backed by the ``job`` table.

What a feature module needs::

    from app.shared.jobs import JobContext, enqueue, job_handler

    @job_handler("hunter.poll_scrape")
    async def poll_scrape(payload: dict, ctx: JobContext) -> None:
        ...

    await enqueue(session, "hunter.poll_scrape", {"search_id": str(search_id)})

An HTTP route never does slow work itself: it enqueues and answers.
"""

from __future__ import annotations

from app.shared.jobs.models import (
    DEFAULT_MAX_ATTEMPTS,
    Job,
    JobStatus,
    backoff_delay,
    next_run_after,
)
from app.shared.jobs.queue import ClaimedJob, enqueue
from app.shared.jobs.registry import (
    DuplicateHandlerError,
    JobContext,
    JobHandler,
    RetryLater,
    UnknownJobTypeError,
    is_registered,
    job_handler,
    load_handler_modules,
    registered_types,
    restore_handlers,
    snapshot_handlers,
)
from app.shared.jobs.runner import run_forever, run_next
from app.shared.jobs.web import router

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "ClaimedJob",
    "DuplicateHandlerError",
    "Job",
    "JobContext",
    "JobHandler",
    "JobStatus",
    "RetryLater",
    "UnknownJobTypeError",
    "backoff_delay",
    "enqueue",
    "is_registered",
    "job_handler",
    "load_handler_modules",
    "next_run_after",
    "registered_types",
    "restore_handlers",
    "router",
    "run_forever",
    "run_next",
    "snapshot_handlers",
]
