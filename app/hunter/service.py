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
    ProspectFilters,
    ProspectStatus,
    ScrapeStatus,
    Search,
    SearchInput,
    SearchStatus,
    deadline_for,
    scrape_parameters,
)
from app.hunter.export import prospects_to_csv, prospects_to_phone_list
from app.hunter.ports import (
    ClientDirectory,
    CompanySearchProvider,
    HunterRepository,
    ScraperError,
    WebsiteInspector,
)
from app.shared.clock import local_day_bounds, now_utc, today_local
from app.shared.events import ProspectConverted, publish

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

    # ── qualificação ──────────────────────────────────────────

    async def search_prospects(
        self, filters: ProspectFilters, *, limit: int = 500
    ) -> list[Prospect]:
        return await self._repository.search_prospects(filters, limit=limit)

    async def count_prospects(self, filters: ProspectFilters) -> int:
        return await self._repository.count_filtered(filters)

    async def get_prospect(self, prospect_id: UUID) -> Prospect | None:
        return await self._repository.get_prospect(prospect_id)

    async def set_prospect_status(
        self, prospect_id: UUID, status: ProspectStatus
    ) -> Prospect | None:
        return await self._repository.set_prospect_status(prospect_id, status)

    async def set_do_not_contact(self, prospect_id: UUID, *, flag: bool = True) -> Prospect | None:
        return await self._repository.set_do_not_contact(prospect_id, flag=flag)

    async def export_csv(self, filters: ProspectFilters, *, limit: int = 5000) -> str:
        return prospects_to_csv(await self._repository.search_prospects(filters, limit=limit))

    async def phone_list(self, filters: ProspectFilters, *, limit: int = 5000) -> list[str]:
        return prospects_to_phone_list(
            await self._repository.search_prospects(filters, limit=limit)
        )

    async def convert_to_client(
        self, prospect_id: UUID, clients: ClientDirectory
    ) -> Prospect | None:
        """Turn a prospect into a client of the agency.

        Already converted: nothing happens again, so a double click does not
        create a second client.
        """
        prospect = await self._repository.get_prospect(prospect_id)
        if prospect is None:
            return None
        if prospect.client_id is not None:
            return prospect

        client = await clients.create_client_from_prospect(
            name=prospect.name,
            phone=prospect.whatsapp or prospect.phone,
            email=prospect.first_email,
            notes=f"Veio do Hunter. {prospect.maps_link}",
        )
        converted = await self._repository.attach_client(prospect_id, client.id)
        publish(ProspectConverted(prospect_id=prospect_id, client_id=client.id))
        logger.info("prospect %s became client %s", prospect_id, client.id)
        return converted

    async def check_sites(self, search_id: UUID, inspector: WebsiteInspector) -> int:
        """Visit the sites of a search's prospects and score them again.

        A site that does not answer is recorded as unreachable and the
        prospect keeps the score it had: silence is not evidence.
        """
        pending = await self._repository.list_for_site_check(search_id)
        if not pending:
            return 0

        findings = await inspector.inspect_many(
            [prospect.website for prospect in pending if prospect.website]
        )

        checked = 0
        for prospect in pending:
            result = findings.get(prospect.website or "")
            if result is None:
                continue
            await self._repository.apply_site_check(
                prospect.id,
                has_meta_pixel=result.has_meta_pixel,
                has_instagram=result.has_instagram,
                has_whatsapp_link=result.has_whatsapp_link,
                unreachable=not result.reachable,
            )
            checked += 1

        logger.info("checked %s site(s) for search %s", checked, search_id)
        return checked
