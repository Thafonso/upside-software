"""Hunter use cases. Repository and provider come in by injection."""

from __future__ import annotations

import logging
from enum import StrEnum
from uuid import UUID

from app.hunter.domain import (
    SCRAPER_FAILURE_MESSAGE,
    TIMEOUT_MESSAGE,
    DailyLimitReached,
    Prospect,
    ScrapeStatus,
    Search,
    SearchInput,
    SearchStatus,
    deadline_for,
    scrape_parameters,
)
from app.hunter.ports import CompanySearchProvider, HunterRepository, ScraperError
from app.shared.clock import local_day_bounds, now_utc, today_local

logger = logging.getLogger("app.hunter")


class StartOutcome(StrEnum):
    """What ``begin_scrape`` did."""

    STARTED = "started"
    BUSY = "busy"
    SKIPPED = "skipped"


class PollOutcome(StrEnum):
    """What ``collect_results`` found."""

    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class SearchNotFound(LookupError):
    """No search with that id."""


class HunterService:
    def __init__(self, repository: HunterRepository, *, daily_limit: int) -> None:
        self._repository = repository
        self._daily_limit = daily_limit

    # ── limite diário ─────────────────────────────────────────

    async def searches_used_today(self) -> int:
        start, end = local_day_bounds(today_local())
        return await self._repository.count_searches_between(start, end)

    async def remaining_today(self) -> int:
        return max(self._daily_limit - await self.searches_used_today(), 0)

    @property
    def daily_limit(self) -> int:
        return self._daily_limit

    # ── buscas ────────────────────────────────────────────────

    async def create_search(self, data: SearchInput) -> Search:
        """Register a search. The job queue does the slow part."""
        used = await self.searches_used_today()
        if used >= self._daily_limit:
            raise DailyLimitReached(used, self._daily_limit)

        depth, max_time = scrape_parameters(data.quantity, fetch_emails=data.fetch_emails)
        return await self._repository.create_search(data, depth=depth, max_time_seconds=max_time)

    async def get_search(self, search_id: UUID) -> Search | None:
        return await self._repository.get_search(search_id)

    async def require_search(self, search_id: UUID) -> Search:
        search = await self._repository.get_search(search_id)
        if search is None:
            raise SearchNotFound(str(search_id))
        return search

    async def list_searches(self, *, limit: int = 50) -> list[Search]:
        return await self._repository.list_searches(limit=limit)

    async def list_prospects(self, search_id: UUID, *, limit: int = 500) -> list[Prospect]:
        return await self._repository.list_prospects(search_id, limit=limit)

    # ── ciclo de vida da raspagem ─────────────────────────────

    async def begin_scrape(self, search_id: UUID, provider: CompanySearchProvider) -> StartOutcome:
        """Hand the search to the scraper. Safe to call twice."""
        search = await self._repository.get_search(search_id)
        if search is None or search.status != SearchStatus.QUEUED:
            return StartOutcome.SKIPPED

        # The scraper drives a browser: one search at a time, or they
        # fight over the same resources and the results get worse.
        busy_with = await self._repository.running_search_id(excluding=search_id)
        if busy_with is not None:
            return StartOutcome.BUSY

        started_at = now_utc()
        scraper_job_id = await provider.start(search.to_request())
        await self._repository.mark_running(
            search_id,
            scraper_job_id=scraper_job_id,
            started_at=started_at,
            deadline_at=deadline_for(started_at, search.max_time_seconds),
        )
        logger.info("search %s started at the scraper as %s", search_id, scraper_job_id)
        return StartOutcome.STARTED

    async def collect_results(
        self, search_id: UUID, provider: CompanySearchProvider
    ) -> PollOutcome:
        """Check on the scraper and import the results once they are ready."""
        search = await self._repository.get_search(search_id)
        if search is None or search.status != SearchStatus.RUNNING:
            return PollOutcome.SKIPPED

        if not search.scraper_job_id:
            await self._repository.mark_failed(
                search_id, message="A busca não chegou a ser enviada ao scraper."
            )
            return PollOutcome.FAILED

        if search.deadline_at is not None and now_utc() > search.deadline_at:
            logger.warning("search %s passed its deadline", search_id)
            await self._repository.mark_failed(search_id, message=TIMEOUT_MESSAGE)
            await self._discard(provider, search.scraper_job_id)
            return PollOutcome.FAILED

        status = await provider.status(search.scraper_job_id)
        if not status.is_finished:
            return PollOutcome.WAITING

        if status == ScrapeStatus.FAILED:
            await self._repository.mark_failed(search_id, message=SCRAPER_FAILURE_MESSAGE)
            await self._discard(provider, search.scraper_job_id)
            return PollOutcome.FAILED

        companies = await provider.results(search.scraper_job_id)
        written = await self._repository.upsert_prospects(search_id, companies)
        await self._repository.mark_done(search_id, total_found=written)
        await self._discard(provider, search.scraper_job_id)
        logger.info("search %s imported %s companies", search_id, written)
        return PollOutcome.DONE

    async def _discard(self, provider: CompanySearchProvider, job_id: str) -> None:
        """Clean up at the scraper. Never fail the import over this."""
        try:
            await provider.discard(job_id)
        except ScraperError:
            logger.warning("could not delete job %s at the scraper", job_id)
