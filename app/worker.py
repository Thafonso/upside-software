"""Background process: drains the job queue and arms the clock triggers.

One job at a time, in order of ``run_after``. Slow work (the scraper, the
Meta API, checking sites) belongs here, never in a web request.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from sqlalchemy import text

from app.shared.config import get_settings
from app.shared.db import dispose_engine, get_sessionmaker
from app.shared.jobs import load_handler_modules, registered_types, run_forever
from app.shared.jobs import queue as job_queue
from app.shared.jobs.scheduler import build_scheduler

logger = logging.getLogger("app.worker")

DB_RETRY_SECONDS = 3.0

# Packages whose import registers queue handlers. Step 5 adds "app.ads".
HANDLER_MODULES: tuple[str, ...] = ("app.hunter",)


async def wait_for_database(*, attempts: int = 20) -> None:
    """Block until Postgres answers, so a cold Compose start is not a failure."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
            logger.info("database reachable")
            return
        except Exception as exc:  # any driver error means "not ready yet"
            last_error = exc
            logger.warning("database not ready (%s/%s)", attempt, attempts)
            await asyncio.sleep(DB_RETRY_SECONDS)
    msg = "database never became reachable"
    raise RuntimeError(msg) from last_error


async def rescue_orphans() -> None:
    """Requeue whatever a previous worker left mid-flight."""
    async with get_sessionmaker()() as session:
        rescued = await job_queue.requeue_orphans(session)
        await session.commit()
    if rescued:
        logger.warning("requeued %s job(s) left running by a previous worker", rescued)


async def run(stop: asyncio.Event) -> None:
    settings = get_settings()
    logger.info("worker starting (env=%s, tz=%s)", settings.app_env, settings.tz)

    await wait_for_database()
    load_handler_modules(HANDLER_MODULES)
    logger.info("handlers: %s", ", ".join(registered_types()) or "nenhum ainda")
    await rescue_orphans()

    sessions = get_sessionmaker()
    scheduler = build_scheduler(sessions)
    scheduler.start()
    try:
        await run_forever(sessions, stop)
    finally:
        scheduler.shutdown(wait=False)

    logger.info("worker stopping")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await run(stop)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
