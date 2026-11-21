"""Clock-driven triggers. The scheduler only ever enqueues.

Nothing slow happens here: each trigger writes one row to ``job`` and the
queue loop picks it up, so a long sync can never hold the scheduler.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.shared.clock import local_timezone
from app.shared.jobs import queue
from app.shared.jobs.registry import is_registered

logger = logging.getLogger("app.jobs.scheduler")


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """A job type the clock enqueues on its own."""

    job_type: str
    hour: str
    minute: str
    description: str

    def trigger(self) -> CronTrigger:
        return CronTrigger(hour=self.hour, minute=self.minute, timezone=local_timezone())


SCHEDULED_JOBS: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        job_type="ads.enqueue_hourly_sync",
        hour="*",
        minute="5",
        description="sincroniza as métricas de hoje de cada conta",
    ),
    ScheduledJob(
        job_type="ads.enqueue_resync_7d",
        hour="3",
        minute="0",
        description="refaz os últimos 7 dias, que a Meta ainda ajusta",
    ),
)


def _enqueue_task(
    sessions: async_sessionmaker[AsyncSession], scheduled: ScheduledJob
) -> Callable[[], Awaitable[None]]:
    async def task() -> None:
        async with sessions() as session:
            job_id = await queue.enqueue(session, scheduled.job_type)
            await session.commit()
        logger.info("scheduled %s enqueued as %s", scheduled.job_type, job_id)

    return task


def build_scheduler(
    sessions: async_sessionmaker[AsyncSession],
    *,
    scheduled_jobs: Sequence[ScheduledJob] = SCHEDULED_JOBS,
) -> AsyncIOScheduler:
    """Arm the triggers whose handler this process actually knows.

    A job type with no handler yet (its step has not been built) would only
    pile up failures, so it is left disarmed and logged.
    """
    scheduler = AsyncIOScheduler(timezone=local_timezone())

    for scheduled in scheduled_jobs:
        if not is_registered(scheduled.job_type):
            logger.info("trigger %s not armed: no handler registered yet", scheduled.job_type)
            continue
        scheduler.add_job(
            _enqueue_task(sessions, scheduled),
            trigger=scheduled.trigger(),
            id=scheduled.job_type,
            name=scheduled.description,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "trigger armed %s (hour=%s minute=%s)",
            scheduled.job_type,
            scheduled.hour,
            scheduled.minute,
        )

    return scheduler
