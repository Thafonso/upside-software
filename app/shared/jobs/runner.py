"""Takes one job off the queue and runs it."""

from __future__ import annotations

import asyncio
import logging
import traceback

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.shared.jobs import queue
from app.shared.jobs.models import JobStatus
from app.shared.jobs.registry import (
    JobContext,
    RetryLater,
    UnknownJobTypeError,
    get_handler,
)

logger = logging.getLogger("app.jobs")

IDLE_SLEEP_SECONDS = 1.0


async def run_next(sessions: async_sessionmaker[AsyncSession]) -> bool:
    """Claim and run one due job. ``False`` means the queue had nothing.

    The claim is committed on its own so the row is marked ``running`` even
    if this process dies; the handler then works in a second transaction,
    together with the write that marks the job done.
    """
    async with sessions() as claiming:
        job = await queue.claim_next(claiming)
        await claiming.commit()

    if job is None:
        return False

    logger.info(
        "job started type=%s id=%s attempt=%s/%s",
        job.type,
        job.id,
        job.attempt_number,
        job.max_attempts,
    )

    try:
        handler = get_handler(job.type)
    except UnknownJobTypeError:
        await _fail(sessions, job, f"nenhum handler registrado para '{job.type}'")
        return True

    async with sessions() as working:
        context = JobContext(
            job_id=job.id,
            job_type=job.type,
            attempt=job.attempt_number,
            max_attempts=job.max_attempts,
            session=working,
        )
        try:
            await handler(job.payload, context)
            await queue.mark_done(working, job.id)
            await working.commit()
        except RetryLater as later:
            await working.rollback()
            await _retry_later(sessions, job, later)
            return True
        except Exception:
            await working.rollback()
            await _fail(sessions, job, traceback.format_exc())
            return True

    logger.info("job done type=%s id=%s", job.type, job.id)
    return True


async def _retry_later(
    sessions: async_sessionmaker[AsyncSession],
    job: queue.ClaimedJob,
    signal: RetryLater,
) -> None:
    async with sessions() as session:
        run_at = await queue.reschedule(session, job.id, delay_seconds=signal.seconds)
        await session.commit()
    logger.info("job waiting type=%s id=%s retry_at=%s", job.type, job.id, run_at.isoformat())


async def _fail(
    sessions: async_sessionmaker[AsyncSession],
    job: queue.ClaimedJob,
    error: str,
) -> None:
    async with sessions() as session:
        outcome = await queue.register_failure(session, job.id, error=error)
        await session.commit()

    if outcome == JobStatus.FAILED:
        logger.error(
            "job failed for good type=%s id=%s attempts=%s",
            job.type,
            job.id,
            job.attempt_number,
        )
    else:
        logger.warning(
            "job failed, will retry type=%s id=%s attempt=%s/%s",
            job.type,
            job.id,
            job.attempt_number,
            job.max_attempts,
        )


async def run_forever(
    sessions: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    *,
    idle_sleep: float = IDLE_SLEEP_SECONDS,
) -> None:
    """Drain the queue one job at a time until ``stop`` is set."""
    while not stop.is_set():
        try:
            worked = await run_next(sessions)
        except Exception:
            logger.exception("queue loop error")
            worked = False

        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle_sleep)
            except TimeoutError:
                continue
