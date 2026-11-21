"""Background process.

Step 1 scope: keep a supervised process alive next to the web app and prove the
database is reachable, so Compose reports a real state instead of a crash loop.
Step 2 adds the job-queue loop and the APScheduler triggers to this same loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from sqlalchemy import text

from app.shared.config import get_settings
from app.shared.db import dispose_engine, get_sessionmaker

logger = logging.getLogger("app.worker")

DB_RETRY_SECONDS = 3.0
HEARTBEAT_SECONDS = 30.0


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


async def run(stop: asyncio.Event) -> None:
    settings = get_settings()
    logger.info("worker starting (env=%s, tz=%s)", settings.app_env, settings.tz)
    await wait_for_database()

    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        if not stop.is_set():
            logger.debug("worker idle")

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
