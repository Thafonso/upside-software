"""Queue handlers for the hunter. Registered by name, idempotent by design."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.hunter.adapters.mazyos_scraper import scraper_provider
from app.hunter.adapters.site_checker import website_inspector
from app.hunter.repository import SqlAlchemyHunterRepository
from app.hunter.service import HunterService, PollOutcome, StartOutcome
from app.shared.config import get_settings
from app.shared.jobs import JobContext, RetryLater, enqueue, job_handler

logger = logging.getLogger("app.hunter.jobs")

START_SCRAPE = "hunter.start_scrape"
POLL_SCRAPE = "hunter.poll_scrape"
CHECK_SITES = "hunter.check_sites"

# Another search is at the scraper: come back when it is likely free.
BUSY_RETRY_SECONDS = 30
# The scraper is still working: short poll, this is the normal path.
POLL_RETRY_SECONDS = 15


def _service(ctx: JobContext) -> HunterService:
    return HunterService(
        SqlAlchemyHunterRepository(ctx.session),
        daily_limit=get_settings().hunter_daily_limit,
    )


def _search_id(payload: dict[str, Any]) -> UUID:
    return UUID(str(payload["search_id"]))


@job_handler(START_SCRAPE)
async def start_scrape(payload: dict[str, Any], ctx: JobContext) -> None:
    """Send a queued search to the scraper and start polling it."""
    search_id = _search_id(payload)
    service = _service(ctx)

    async with scraper_provider() as provider:
        outcome = await service.begin_scrape(search_id, provider)

    if outcome == StartOutcome.BUSY:
        # Not a failure: the queue holds this search until the other finishes.
        raise RetryLater(seconds=BUSY_RETRY_SECONDS)

    if outcome == StartOutcome.SKIPPED:
        logger.info("search %s was already started, nothing to do", search_id)
        return

    await enqueue(ctx.session, POLL_SCRAPE, {"search_id": str(search_id)})


@job_handler(POLL_SCRAPE)
async def poll_scrape(payload: dict[str, Any], ctx: JobContext) -> None:
    """Wait on the scraper, then import what it found."""
    search_id = _search_id(payload)
    service = _service(ctx)

    async with scraper_provider() as provider:
        outcome = await service.collect_results(search_id, provider)

    if outcome == PollOutcome.WAITING:
        raise RetryLater(seconds=POLL_RETRY_SECONDS)

    if outcome != PollOutcome.DONE:
        return

    await enqueue(ctx.session, CHECK_SITES, {"search_id": str(search_id)})


@job_handler(CHECK_SITES)
async def check_sites(payload: dict[str, Any], ctx: JobContext) -> None:
    """Visit the prospects' own sites and score them again.

    Idempotent: only prospects never visited are picked up, so running the
    job twice costs nothing and a crash halfway simply resumes.
    """
    search_id = _search_id(payload)
    service = _service(ctx)

    async with website_inspector() as inspector:
        checked = await service.check_sites(search_id, inspector)

    logger.info("search %s had %s site(s) checked", search_id, checked)
