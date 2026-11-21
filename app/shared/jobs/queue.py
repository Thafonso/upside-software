"""Queue operations against the ``job`` table.

Every function takes the session it should work in, so enqueuing can share
the caller's transaction: a search row and the job that processes it are
either both committed or neither is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.clock import now_utc
from app.shared.jobs.models import DEFAULT_MAX_ATTEMPTS, Job, JobStatus, next_run_after

MAX_ERROR_LENGTH = 4000


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A job handed to the runner, detached from the claiming transaction."""

    id: UUID
    type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int

    @property
    def attempt_number(self) -> int:
        """1 on the first run: ``attempts`` counts the failures so far."""
        return self.attempts + 1


async def enqueue(
    session: AsyncSession,
    job_type: str,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> UUID:
    """Put a job on the queue. The caller commits."""
    job = Job(
        type=job_type,
        payload=payload or {},
        status=JobStatus.QUEUED,
        attempts=0,
        max_attempts=max_attempts,
        run_after=run_after or now_utc(),
    )
    session.add(job)
    await session.flush()
    return job.id


async def claim_next(session: AsyncSession, *, now: datetime | None = None) -> ClaimedJob | None:
    """Take the next due job and mark it running.

    ``FOR UPDATE SKIP LOCKED`` lets more than one worker pull from the queue
    without ever handing the same row to two of them.
    """
    moment = now or now_utc()
    statement = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.run_after <= moment)
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(statement)).scalar_one_or_none()
    if job is None:
        return None

    job.status = JobStatus.RUNNING
    await session.flush()
    return ClaimedJob(
        id=job.id,
        type=job.type,
        payload=dict(job.payload or {}),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
    )


async def mark_done(session: AsyncSession, job_id: UUID) -> None:
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=JobStatus.DONE, last_error=None, updated_at=now_utc())
    )


async def reschedule(session: AsyncSession, job_id: UUID, *, delay_seconds: int) -> datetime:
    """Put the job back without counting an attempt (``RetryLater``)."""
    run_at = now_utc() + timedelta(seconds=max(delay_seconds, 1))
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=JobStatus.QUEUED, run_after=run_at, updated_at=now_utc())
    )
    return run_at


async def register_failure(session: AsyncSession, job_id: UUID, *, error: str) -> JobStatus:
    """Count the attempt and either schedule a retry or give up."""
    job = await session.get(Job, job_id)
    if job is None:
        return JobStatus.FAILED

    moment = now_utc()
    job.attempts += 1
    job.last_error = error[-MAX_ERROR_LENGTH:]

    if job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.QUEUED
        job.run_after = next_run_after(moment, job.attempts)

    await session.flush()
    return JobStatus(job.status)


async def requeue(session: AsyncSession, job_id: UUID) -> bool:
    """Admin action: send a failed job back to the queue, attempts reset."""
    job = await session.get(Job, job_id)
    if job is None or JobStatus(job.status) != JobStatus.FAILED:
        return False

    job.status = JobStatus.QUEUED
    job.attempts = 0
    job.last_error = None
    job.run_after = now_utc()
    await session.flush()
    return True


async def requeue_orphans(session: AsyncSession) -> int:
    """Rescue jobs left ``running`` by a worker that died mid-handler.

    Called at worker start-up: with a single worker process, anything still
    running at that point has no one working on it.
    """
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(Job)
            .where(Job.status == JobStatus.RUNNING)
            .values(status=JobStatus.QUEUED, run_after=now_utc(), updated_at=now_utc())
        ),
    )
    return result.rowcount or 0


async def get_job(session: AsyncSession, job_id: UUID) -> Job | None:
    return await session.get(Job, job_id)


async def list_recent(
    session: AsyncSession,
    *,
    status: JobStatus | None = None,
    job_type: str | None = None,
    limit: int = 50,
) -> list[Job]:
    statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status is not None:
        statement = statement.where(Job.status == status)
    if job_type:
        statement = statement.where(Job.type == job_type)
    return list((await session.execute(statement)).scalars().all())


async def count_by_status(session: AsyncSession) -> dict[JobStatus, int]:
    rows = await session.execute(select(Job.status, func.count()).group_by(Job.status))
    counts = dict.fromkeys(JobStatus, 0)
    for status, total in rows:
        counts[JobStatus(status)] = total
    return counts
